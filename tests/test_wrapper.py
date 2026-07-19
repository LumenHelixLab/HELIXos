"""Negative security suite for the KNOTstore wrapper (audit M1 exit criteria, SPEC §4).

Every case pins a fail-closed property of generate_triangulated_instruction /
verify_instruction_lock: valid roundtrips verify True (HMAC and Ed25519), and
forgery, verb-swap, ptr-swap, replay, stale timestamps, malformed lines,
tampered envelopes, phase mismatches, oversized payloads and invalid verbs all
fail. The golden-vector test pins exact bus lines for deterministic inputs.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

import knotcore_sim
import KNOT_API_WRAPPER as W
from signers import (
    Ed25519Signer,
    Ed25519Verifier,
    HMACSigner,
    HMACVerifier,
)

# TEST-ONLY dev key material (never production; secrets come from env per SPEC §3.10).
HMAC_KEY = bytes(range(32))
WRONG_HMAC_KEY = b"\xff" * 32
ED_SEED = b"\x42" * 32
WRONG_ED_SEED = b"\x24" * 32

PTR_OF = re.compile(r"^\[ (?P<ptr>[0-9a-f]{16})")


def _ptr_of(line: str) -> str:
    return PTR_OF.match(line).group("ptr")  # type: ignore[union-attr]


@pytest.fixture(autouse=True)
def clean_state():
    """Isolate the simulator store and the wrapper replay cache between tests."""
    knotcore_sim.reset_store()
    with W._seen_lock:
        W._seen_nonces.clear()
    yield
    knotcore_sim.reset_store()
    with W._seen_lock:
        W._seen_nonces.clear()


@pytest.fixture
def hmac_signer():
    return HMACSigner(HMAC_KEY)


@pytest.fixture
def hmac_verifier():
    return HMACVerifier(HMAC_KEY)


@pytest.fixture
def ed_signer():
    return Ed25519Signer(ED_SEED)


@pytest.fixture
def ed_verifier(ed_signer):
    return Ed25519Verifier(ed_signer.public_key_bytes())


class MutableBackend:
    """In-memory KnotBackend whose stored envelopes tests can tamper with."""

    def __init__(self) -> None:
        self.slots: dict[str, str] = {}
        self.generations: dict[str, int] = {}
        self._next = 0

    def store_instruction(self, payload: str) -> str:
        ptr = f"{self._next:016x}"
        self._next += 1
        self.slots[ptr] = payload
        self.generations[ptr] = 0
        return ptr

    def fetch_payload(self, ptr: str) -> str | None:
        return self.slots.get(ptr)

    def verify_cauldron_phase(self, ptr: str, tag: str, phase: int) -> bool:
        return ptr in self.slots and self.generations[ptr] == phase


# ---------------------------------------------------------------------------
# Valid roundtrips
# ---------------------------------------------------------------------------


def test_valid_roundtrip_hmac(hmac_signer, hmac_verifier):
    line = W.generate_triangulated_instruction("hello world", "EXEC", hmac_signer)
    assert W.BUS_RE.fullmatch(line)
    assert len(W.BUS_RE.fullmatch(line).group("tag")) == 22  # type: ignore[union-attr]
    assert W.verify_instruction_lock(line, hmac_verifier) is True


def test_valid_roundtrip_ed25519(ed_signer, ed_verifier):
    line = W.generate_triangulated_instruction("boot sequence", "EXEC", ed_signer)
    assert W.BUS_RE.fullmatch(line)
    assert len(W.BUS_RE.fullmatch(line).group("tag")) == 86  # type: ignore[union-attr]
    assert W.verify_instruction_lock(line, ed_verifier) is True


def test_roundtrip_normalizes_verb_case(hmac_signer, hmac_verifier):
    line = W.generate_triangulated_instruction("doc", "read", hmac_signer)
    assert " | READ | " in line
    assert W.verify_instruction_lock(line, hmac_verifier) is True


def test_unicode_payload_roundtrip(hmac_signer, hmac_verifier):
    line = W.generate_triangulated_instruction("λ-メモリ-✓", "WRITE", hmac_signer)
    assert W.verify_instruction_lock(line, hmac_verifier) is True


# ---------------------------------------------------------------------------
# Forgery
# ---------------------------------------------------------------------------


def test_forgery_wrong_hmac_key(hmac_signer):
    line = W.generate_triangulated_instruction("hello", "READ", hmac_signer)
    assert W.verify_instruction_lock(line, HMACVerifier(WRONG_HMAC_KEY)) is False


def test_forgery_wrong_ed25519_key(ed_signer):
    line = W.generate_triangulated_instruction("hello", "READ", ed_signer)
    wrong_pub = Ed25519Signer(WRONG_ED_SEED).public_key_bytes()
    assert W.verify_instruction_lock(line, Ed25519Verifier(wrong_pub)) is False


def test_forgery_cross_mode_tag_length(hmac_signer, hmac_verifier, ed_signer, ed_verifier):
    # An Ed25519 line presented to an HMAC verifier (and vice versa) fails on
    # the per-mode tag-length check, closed.
    ed_line = W.generate_triangulated_instruction("a", "READ", ed_signer)
    hmac_line = W.generate_triangulated_instruction("b", "READ", hmac_signer)
    assert W.verify_instruction_lock(ed_line, hmac_verifier) is False
    assert W.verify_instruction_lock(hmac_line, ed_verifier) is False


def test_forgery_flipped_tag_char(hmac_signer, hmac_verifier):
    line = W.generate_triangulated_instruction("hello", "READ", hmac_signer)
    tag = W.BUS_RE.fullmatch(line).group("tag")  # type: ignore[union-attr]
    flipped = ("A" if tag[0] != "A" else "B") + tag[1:]
    forged = line.replace(f"#{tag}", f"#{flipped}")
    assert forged != line
    assert W.verify_instruction_lock(forged, hmac_verifier) is False


# ---------------------------------------------------------------------------
# Axis-binding swaps (AUD-C4)
# ---------------------------------------------------------------------------


def test_verb_swap_rejected(hmac_signer, hmac_verifier):
    line = W.generate_triangulated_instruction("payload", "EXEC", hmac_signer)
    swapped = line.replace(" | EXEC | ", " | READ | ")
    assert swapped != line
    assert W.verify_instruction_lock(swapped, hmac_verifier) is False


def test_ptr_swap_rejected(hmac_signer, hmac_verifier):
    line1 = W.generate_triangulated_instruction("payload-one", "READ", hmac_signer)
    line2 = W.generate_triangulated_instruction("payload-two", "READ", hmac_signer)
    ptr2 = _ptr_of(line2)
    swapped = line1.replace(_ptr_of(line1), ptr2, 1)
    assert swapped != line1
    assert W.verify_instruction_lock(swapped, hmac_verifier) is False


# ---------------------------------------------------------------------------
# Replay and freshness (AUD-C5)
# ---------------------------------------------------------------------------


def test_replay_rejected(hmac_signer, hmac_verifier):
    line = W.generate_triangulated_instruction("hello", "READ", hmac_signer)
    assert W.verify_instruction_lock(line, hmac_verifier) is True
    assert W.verify_instruction_lock(line, hmac_verifier) is False  # same line twice


def test_stale_timestamp_rejected(hmac_signer, hmac_verifier):
    old = time.time() - (W.MAX_CLOCK_SKEW_S + 1000)
    line = W.generate_triangulated_instruction("hello", "READ", hmac_signer, _now=old)
    assert W.verify_instruction_lock(line, hmac_verifier) is False


def test_future_timestamp_beyond_skew_rejected(hmac_signer, hmac_verifier):
    future = time.time() + (W.MAX_CLOCK_SKEW_S + 1000)
    line = W.generate_triangulated_instruction("hello", "READ", hmac_signer, _now=future)
    assert W.verify_instruction_lock(line, hmac_verifier) is False


def test_failed_attempt_does_not_burn_nonce(hmac_signer, hmac_verifier):
    # A wrong-phase attempt must not admit the nonce: the same instruction
    # still verifies once the right phase is supplied.
    line = W.generate_triangulated_instruction("hello", "READ", hmac_signer)
    assert W.verify_instruction_lock(line, hmac_verifier, expected_phase=9) is False
    assert W.verify_instruction_lock(line, hmac_verifier, expected_phase=0) is True


# ---------------------------------------------------------------------------
# Malformed lines (AUD-M1)
# ---------------------------------------------------------------------------


@pytest.fixture
def valid_line(hmac_signer):
    return W.generate_triangulated_instruction("hello world", "READ", hmac_signer)


def _mangled_cases(valid_line: str) -> list:
    ptr = _ptr_of(valid_line)
    tag = W.BUS_RE.fullmatch(valid_line).group("tag")  # type: ignore[union-attr]
    return [
        "garbage",
        "",
        "   ",
        "[ 0000000000000000 | READ | nohash ]",                      # missing '#'
        f"[ {ptr[:-1]} | READ | #{tag} ]",                           # ptr too short (15)
        f"[ {ptr}0 | READ | #{tag} ]",                               # ptr too long (17)
        f"[ {'g' * 16} | READ | #{tag} ]",                           # non-hex ptr
        f"[ ABCDEF0123456789 | READ | #{tag} ]",                     # uppercase hex ptr
        valid_line.replace(" | READ | ", " | read | "),              # lowercase verb
        valid_line.replace(" | READ | ", " | RM\u202eRF | "),        # bidi control in verb
        valid_line.replace(" | READ | ", " | DELETE | "),            # non-allowlisted verb
        valid_line.replace(f"#{tag}", f"#{tag[:-1]}"),               # tag 21 chars
        valid_line.replace(f"#{tag}", f"#{tag}A" * 4),               # tag 88 chars
        valid_line.replace(f"#{tag}", "#" + tag[:10] + "!" + tag[11:]),  # bad tag char
        valid_line[:-2],                                             # truncated line
        valid_line + " extra",                                       # trailing junk (no strip rescue)
        None,                                                        # non-str input
        b"[ 0000000000000000 | READ | #AAAA ]",                      # bytes input
    ]


def test_malformed_lines_rejected(valid_line, hmac_verifier):
    for case in _mangled_cases(valid_line):
        assert W.verify_instruction_lock(case, hmac_verifier) is False, repr(case)


def test_valid_line_with_surrounding_whitespace_still_verifies(valid_line, hmac_verifier):
    # strip() is intentional: transport padding is not part of the grammar.
    assert W.verify_instruction_lock(f"  {valid_line}  ", hmac_verifier) is True


# ---------------------------------------------------------------------------
# Envelope tampering
# ---------------------------------------------------------------------------


def test_tampered_envelope_body_rejected(hmac_signer, hmac_verifier):
    backend = MutableBackend()
    line = W.generate_triangulated_instruction("hello world", "READ", hmac_signer, backend=backend)
    ptr = _ptr_of(line)
    backend.slots[ptr] = backend.slots[ptr].replace("world", "evil")
    assert W.verify_instruction_lock(line, hmac_verifier, backend=backend) is False


def test_tampered_envelope_nonce_rejected(hmac_signer, hmac_verifier):
    backend = MutableBackend()
    line = W.generate_triangulated_instruction("hello", "READ", hmac_signer, backend=backend)
    ptr = _ptr_of(line)
    env = json.loads(backend.slots[ptr])
    env["nonce"] = "AAAAAAAAAAAAAAAA"  # different 12-byte nonce, same tag on the line
    backend.slots[ptr] = json.dumps(env, separators=(",", ":"))
    assert W.verify_instruction_lock(line, hmac_verifier, backend=backend) is False


def test_unknown_ptr_rejected(hmac_signer, hmac_verifier):
    line = W.generate_triangulated_instruction("hello", "READ", hmac_signer)
    ghost = line.replace(_ptr_of(line), "ffffffffffffffff", 1)
    assert W.verify_instruction_lock(ghost, hmac_verifier) is False


# ---------------------------------------------------------------------------
# Cauldron phase (AUD-H3)
# ---------------------------------------------------------------------------


def test_phase_mismatch_rejected(hmac_signer, hmac_verifier):
    line = W.generate_triangulated_instruction("hello", "READ", hmac_signer)
    assert W.verify_instruction_lock(line, hmac_verifier, expected_phase=1) is False
    assert W.verify_instruction_lock(line, hmac_verifier, expected_phase=0) is True


def test_phase_invalidated_by_generation_bump(hmac_signer, hmac_verifier):
    line = W.generate_triangulated_instruction("hello", "READ", hmac_signer)
    ptr = _ptr_of(line)
    assert knotcore_sim.bump_generation(ptr) == 1
    assert W.verify_instruction_lock(line, hmac_verifier, expected_phase=0) is False
    assert W.verify_instruction_lock(line, hmac_verifier, expected_phase=1) is True


# ---------------------------------------------------------------------------
# Generation-time input validation (dev-visible ValueErrors)
# ---------------------------------------------------------------------------


def test_oversized_payload_rejected(hmac_signer):
    with pytest.raises(ValueError, match="cap"):
        W.generate_triangulated_instruction("x" * (W.MAX_PAYLOAD_BYTES + 1), "READ", hmac_signer)
    # boundary: exactly at the cap is accepted
    line = W.generate_triangulated_instruction("x" * W.MAX_PAYLOAD_BYTES, "READ", hmac_signer)
    assert W.BUS_RE.fullmatch(line)


@pytest.mark.parametrize(
    "verb",
    ["DELETE", "", "   ", "EXEC|RM", "RM\u202eRF", "RE\nAD", "🔥", "WRITE]"],
)
def test_invalid_verb_rejected_at_generation(hmac_signer, verb):
    with pytest.raises(ValueError, match="allowlist|non-empty|must be str"):
        W.generate_triangulated_instruction("payload", verb, hmac_signer)


def test_edge_whitespace_in_verb_is_normalized(hmac_signer):
    # Audit-reference behavior: only EDGE whitespace is stripped (" read " ->
    # "READ"); the emitted bus line carries the canonical verb, so no grammar
    # injection is possible. Embedded control chars stay rejected (see above).
    line = W.generate_triangulated_instruction("payload", "  read\t", hmac_signer)
    assert " | READ | " in line


def test_non_string_verb_rejected(hmac_signer):
    with pytest.raises(ValueError):
        W.generate_triangulated_instruction("payload", 123, hmac_signer)


def test_empty_payload_rejected(hmac_signer):
    with pytest.raises(ValueError):
        W.generate_triangulated_instruction("", "READ", hmac_signer)
    with pytest.raises(ValueError):
        W.generate_triangulated_instruction("   ", "READ", hmac_signer)


def test_invalid_signer_rejected_at_generation():
    with pytest.raises(ValueError, match="signer"):
        W.generate_triangulated_instruction("payload", "READ", None)
    with pytest.raises(ValueError, match="signer"):
        W.generate_triangulated_instruction("payload", "READ", object())


def test_signer_key_validation():
    with pytest.raises(ValueError, match=">= 32"):
        HMACSigner(b"short")
    with pytest.raises(ValueError, match="bytes"):
        HMACSigner("not-bytes")
    with pytest.raises(ValueError, match="32-byte seed"):
        Ed25519Signer(b"\x00" * 31)
    with pytest.raises(ValueError, match="32 raw bytes"):
        Ed25519Verifier(b"\x00" * 33)


def test_storefull_propagates_at_generation(hmac_signer):
    knotcore_sim.configure(1)
    try:
        W.generate_triangulated_instruction("one", "READ", hmac_signer)
        with pytest.raises(knotcore_sim.StoreFull):
            W.generate_triangulated_instruction("two", "READ", hmac_signer)
    finally:
        knotcore_sim.configure()


def test_verify_returns_false_when_backend_raises(hmac_signer, hmac_verifier):
    class ExplodingBackend:
        def store_instruction(self, payload):
            raise RuntimeError("boom")

        def fetch_payload(self, ptr):
            raise RuntimeError("boom")

        def verify_cauldron_phase(self, ptr, tag, phase):
            raise RuntimeError("boom")

    line = W.generate_triangulated_instruction("hello", "READ", hmac_signer)
    assert W.verify_instruction_lock(line, hmac_verifier, backend=ExplodingBackend()) is False


# ---------------------------------------------------------------------------
# Golden vectors
# ---------------------------------------------------------------------------

_VECTORS_PATH = Path(__file__).parent / "vectors" / "golden_vectors.json"


def _load_vectors() -> list[dict]:
    doc = json.loads(_VECTORS_PATH.read_text(encoding="utf-8"))
    assert doc["schema"] == "helixos-golden-vectors/1"
    return doc["vectors"]


def _signer_for(mode: str, key_material_hex: str):
    raw = bytes.fromhex(key_material_hex)
    return HMACSigner(raw) if mode == "hmac" else Ed25519Signer(raw)


def test_golden_vectors_cover_both_modes():
    vectors = _load_vectors()
    assert len(vectors) >= 6, "SPEC requires at least 6 pinned vectors"
    modes = {v["mode"] for v in vectors}
    assert modes == {"hmac", "ed25519"}


@pytest.mark.parametrize("vec", _load_vectors(), ids=lambda v: v["name"])
def test_golden_vector_exact_bus_line(vec):
    knotcore_sim.reset_store()  # first slot -> deterministic ptr
    signer = _signer_for(vec["mode"], vec["key_material"])
    line = W.generate_triangulated_instruction(
        vec["payload"],
        vec["verb"],
        signer,
        _now=vec["ts"],
        _nonce=bytes.fromhex(vec["nonce_hex"]),
    )
    assert line.startswith(vec["expected_bus_line_prefix"]), vec["name"]
    assert W.BUS_RE.fullmatch(line), vec["name"]


@pytest.mark.parametrize("vec", _load_vectors(), ids=lambda v: v["name"])
def test_golden_vector_generation_is_deterministic(vec):
    signer = _signer_for(vec["mode"], vec["key_material"])
    lines = []
    for _ in range(2):
        knotcore_sim.reset_store()
        lines.append(
            W.generate_triangulated_instruction(
                vec["payload"],
                vec["verb"],
                signer,
                _now=vec["ts"],
                _nonce=bytes.fromhex(vec["nonce_hex"]),
            )
        )
    assert lines[0] == lines[1], vec["name"]
