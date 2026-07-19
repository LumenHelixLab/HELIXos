"""reference simulator for the proprietary knotcore.so — CI runs without the binary (ADR-002)

Models the blackbox ABI (SPEC §3.2) so the wrapper, the sidecar and the
contract tests run without the proprietary library:

- 64-bit write-once slots addressed by a 16-lowercase-hex-char pointer;
  a slot, once allocated, is never overwritten or reclaimed.
- Each slot carries a monotonically increasing generation counter (the
  "cauldron phase", starting at 0); ``bump_generation`` advances it, which
  invalidates older tags' phase checks.

Fixes audit findings (see ../HELIXos_Handoff_Audit.md):
- AUD-H2 (256-slot ring aliasing + mass-reset DoS): write-once 64-bit slots
  with generation counters kill pointer aliasing; exhaustion raises StoreFull
  instead of silently wrapping.
- AUD-H6 (unversioned in-process blackbox): this module is the testable
  reference behind the versioned sidecar ABI (sidecar_server.py) and the
  golden-vector contract tests.

Thread-safe: all public functions serialize on one module lock.
"""

from __future__ import annotations

import re
import threading

__all__ = [
    "DEFAULT_CAPACITY",
    "StoreFull",
    "store_instruction",
    "fetch_payload",
    "verify_cauldron_phase",
    "bump_generation",
    "reset_store",
    "configure",
]

DEFAULT_CAPACITY = 2**16
_MAX_SLOTS = 2**64

_PTR_RE = re.compile(r"[0-9a-f]{16}")


class StoreFull(RuntimeError):
    """Raised by store_instruction when the store has no free slots."""


class _State:
    """Mutable module state, grouped so reset/configure stay atomic under the lock."""

    def __init__(self) -> None:
        self.capacity = DEFAULT_CAPACITY
        self.slots: dict[int, list[object]] = {}  # index -> [payload: str, generation: int]
        self.next_index = 0


_lock = threading.Lock()
_state = _State()


def _parse_ptr(ptr: str, what: str = "pointer") -> int:
    if not isinstance(ptr, str) or not _PTR_RE.fullmatch(ptr):
        raise ValueError(f"{what} must be 16 lowercase hex chars, got {ptr!r}")
    return int(ptr, 16)


def store_instruction(payload: str) -> str:
    """Store ``payload`` in the next free write-once slot; return its 16-hex ptr.

    Raises ValueError on a non-str payload and StoreFull when capacity is
    exhausted (or the 64-bit address space wraps, which write-once semantics
    forbid reusing).
    """
    if not isinstance(payload, str):
        raise ValueError(f"payload must be str, got {type(payload).__name__}")
    with _lock:
        if len(_state.slots) >= _state.capacity:
            raise StoreFull(
                f"KNOTstore capacity exhausted ({_state.capacity} slots)"
            )
        index = _state.next_index
        if index in _state.slots:  # 64-bit wraparound: write-once forbids reuse
            raise StoreFull("KNOTstore address space exhausted")
        _state.next_index = (index + 1) % _MAX_SLOTS
        _state.slots[index] = [payload, 0]
        return f"{index:016x}"


def fetch_payload(ptr: str) -> str | None:
    """Return the stored payload for ``ptr``; None for unknown or malformed ptrs."""
    if not isinstance(ptr, str) or not _PTR_RE.fullmatch(ptr):
        return None
    with _lock:
        slot = _state.slots.get(int(ptr, 16))
        return None if slot is None else slot[0]  # type: ignore[return-value]


def verify_cauldron_phase(ptr: str, tag: str, phase: int) -> bool:
    """True iff ``ptr`` exists AND ``phase`` equals the slot's current generation.

    ``tag`` is accepted for ABI compatibility with the proprietary blackbox
    (which anchors the tag in the slot); the simulator has nothing to anchor
    and ignores it. Any malformed input returns False rather than raising.
    """
    if not isinstance(ptr, str) or not _PTR_RE.fullmatch(ptr):
        return False
    if not isinstance(tag, str) or isinstance(phase, bool) or not isinstance(phase, int):
        return False
    with _lock:
        slot = _state.slots.get(int(ptr, 16))
        return slot is not None and slot[1] == phase


def bump_generation(ptr: str) -> int:
    """Advance ``ptr``'s generation counter; returns the new generation.

    Raises ValueError on a malformed pointer and KeyError on an unknown one
    (caller-visible misuse, unlike the fail-closed query functions).
    """
    index = _parse_ptr(ptr)
    with _lock:
        slot = _state.slots.get(index)
        if slot is None:
            raise KeyError(f"unknown KNOTstore pointer {ptr!r}")
        slot[1] = int(slot[1]) + 1
        return int(slot[1])


def reset_store() -> None:
    """Clear all slots and rewind the allocator. TESTS ONLY. Capacity is kept."""
    with _lock:
        _state.slots.clear()
        _state.next_index = 0


def configure(capacity: int = DEFAULT_CAPACITY) -> None:
    """Set slot capacity and clear the store (config-time/tests only).

    Capacity is the ctor-style bound on live slots; changing it always resets
    contents so no slot can outlive the bound it was allocated under.
    """
    if isinstance(capacity, bool) or not isinstance(capacity, int):
        raise ValueError(f"capacity must be an int, got {type(capacity).__name__}")
    if not 1 <= capacity <= _MAX_SLOTS:
        raise ValueError(f"capacity must be in [1, 2**64], got {capacity}")
    with _lock:
        _state.capacity = capacity
        _state.slots.clear()
        _state.next_index = 0
