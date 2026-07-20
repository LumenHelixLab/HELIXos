"""Tests for the AKASH braid (SPEC-M1 §1; verifiable AKASH braid signatures).

Covers: hand-computed commitment hashes, chain linkage, root determinism
(no wall-clock input to commitments), bit-flip sensitivity, crossing weaves
across 2-3 strands, every BraidError validation path, from_events rebuild +
tamper detection, sign_root/verify_root_signature in both signer modes
(ADR-004), and anchor_braid/verify_anchor end-to-end against SimBackend.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import asdict

import pytest

import knotcore_sim
import KNOT_API_WRAPPER as W
from braid import (
    DOMAIN_SEPARATOR,
    GENESIS_TIP,
    Braid,
    BraidError,
    anchor_braid,
    canonical_json,
    sign_root,
    verify_anchor,
    verify_root_signature,
)
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

# Hand-built lines that fullmatch BUS_RE without touching the store (22-char tags).
FIXED_LINE = "[ 0123456789abcdef | EXEC | #AAAAAAAAAAAAAAAAAAAAAA ]"
FIXED_LINE_2 = "[ 0123456789abcde0 | EXEC | #AAAAAAAAAAAAAAAAAAAAAA ]"
FIXED_LINE_3 = "[ 0123456789abcdef | READ | #BBBBBBBBBBBBBBBBBBBBBB ]"


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
def backend():
    return W.SimBackend()


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


def _bus_line(signer, backend, payload: str, verb: str = "EXEC") -> str:
    return W.generate_triangulated_instruction(payload, verb, signer, backend)


def _events_of(braid: Braid) -> list[dict]:
    """Journal-style braid.commit events, as the FSM executor would journal them."""
    return [
        {
            "seq": i + 1,
            "ts": 1_700_000_000.0 + i,
            "epoch": 0,
            "type": "braid.commit",
            "payload": asdict(c),
            "prev": "0" * 64,
            "hash": "f" * 64,
        }
        for i, c in enumerate(braid.to_list())
    ]


def _woven_braid() -> Braid:
    """A 3-strand braid with crossings (shared by rebuild/determinism tests)."""
    b = Braid()
    k1 = b.commit(1, "krishna", FIXED_LINE)
    n1 = b.commit(2, "natasha", FIXED_LINE_2)
    a1 = b.commit(3, "arjuna", FIXED_LINE_3, crossings={"krishna": k1.hash, "natasha": n1.hash})
    b.commit(4, "natasha", FIXED_LINE, crossings={"krishna": k1.hash, "arjuna": a1.hash})
    return b


# ---------------------------------------------------------------------------
# canonical_json / commitment hashing / chain linkage
# ---------------------------------------------------------------------------


def test_canonical_json_sorts_keys_and_compacts():
    assert canonical_json({"b": 1, "a": {"d": 2, "c": 3}}) == '{"a":{"c":3,"d":2},"b":1}'


def test_commitment_hash_hand_computed():
    b = Braid()
    c = b.commit(1, "krishna", FIXED_LINE)
    expected = hashlib.sha256(
        canonical_json(
            {
                "seq": 1,
                "strand": "krishna",
                "bus_line": FIXED_LINE,
                "prev": "0" * 64,
                "crossings": {},
            }
        ).encode("utf-8")
    ).hexdigest()
    assert c.hash == expected
    assert c.seq == 1 and c.strand == "krishna" and c.bus_line == FIXED_LINE
    assert c.prev == GENESIS_TIP == "0" * 64
    assert c.crossings == {}


def test_chain_linkage_prev_equals_prior_tip():
    b = Braid()
    c1 = b.commit(1, "krishna", FIXED_LINE)
    c2 = b.commit(2, "krishna", FIXED_LINE_2)
    c3 = b.commit(3, "krishna", FIXED_LINE_3)
    assert c2.prev == c1.hash
    assert c3.prev == c2.hash
    assert b.tip("krishna") == c3.hash


def test_genesis_tips_for_new_strands():
    b = Braid()
    assert b.tip("unborn") == "0" * 64
    c = b.commit(1, "natasha", FIXED_LINE)
    assert c.prev == "0" * 64
    assert b.tip("natasha") == c.hash
    assert b.tip("unborn") == "0" * 64  # untouched strands stay genesis


def test_empty_braid_root_is_sha256_of_empty_object():
    b = Braid()
    assert b.root() == hashlib.sha256(b"{}").hexdigest()
    assert b.strands() == []
    assert b.to_list() == []


# ---------------------------------------------------------------------------
# root determinism and sensitivity
# ---------------------------------------------------------------------------


def test_root_deterministic_same_ops_regardless_of_wall_clock(monkeypatch):
    b1 = _woven_braid()
    root1 = b1.root()
    monkeypatch.setattr(time, "time", lambda: 9_999_999_999.0)
    b2 = _woven_braid()
    assert b2.root() == root1  # commitments carry no ts: wall clock is irrelevant
    assert [asdict(c) for c in b2.to_list()] == [asdict(c) for c in b1.to_list()]


@pytest.mark.parametrize(
    "seq,strand,bus_line",
    [
        (2, "krishna", FIXED_LINE),      # seq bit flip
        (1, "natasha", FIXED_LINE),      # strand bit flip
        (1, "krishna", FIXED_LINE_2),    # ptr bit flip
        (1, "krishna", FIXED_LINE_3),    # verb+tag bit flip
    ],
)
def test_root_changes_on_any_bit_flip(seq, strand, bus_line):
    base = Braid()
    base.commit(1, "krishna", FIXED_LINE)
    variant = Braid()
    variant.commit(seq, strand, bus_line)
    assert variant.root() != base.root()


def test_root_is_sorted_over_strand_tips():
    b1 = Braid()
    b1.commit(1, "aaa", FIXED_LINE)
    b1.commit(2, "bbb", FIXED_LINE)
    expected = hashlib.sha256(
        canonical_json({"aaa": b1.tip("aaa"), "bbb": b1.tip("bbb")}).encode("utf-8")
    ).hexdigest()
    assert b1.root() == expected


# ---------------------------------------------------------------------------
# crossings
# ---------------------------------------------------------------------------


def test_crossings_weave_two_strands():
    b = Braid()
    k1 = b.commit(1, "krishna", FIXED_LINE)
    n1 = b.commit(2, "natasha", FIXED_LINE_2, crossings={"krishna": k1.hash})
    k2 = b.commit(3, "krishna", FIXED_LINE_3, crossings={"natasha": n1.hash})
    assert n1.crossings == {"krishna": k1.hash}
    assert k2.crossings == {"natasha": n1.hash}
    assert k2.prev == k1.hash  # own-strand linkage preserved alongside crossings
    assert b.tip("krishna") == k2.hash
    assert b.tip("natasha") == n1.hash


def test_crossings_weave_three_strands():
    b = _woven_braid()
    a1 = b.to_list()[2]
    n2 = b.to_list()[3]
    assert a1.strand == "arjuna"
    assert a1.crossings == {
        "krishna": b.to_list()[0].hash,
        "natasha": b.to_list()[1].hash,
    }  # sorted keys
    assert n2.crossings == {"arjuna": a1.hash, "krishna": b.to_list()[0].hash}
    assert n2.prev == b.to_list()[1].hash
    # root is exactly sha256 over the three tips
    expected = hashlib.sha256(
        canonical_json(
            {"arjuna": a1.hash, "krishna": b.to_list()[0].hash, "natasha": n2.hash}
        ).encode("utf-8")
    ).hexdigest()
    assert b.root() == expected
    # crossing changes the commitment hash vs. an uncrossed twin
    twin = Braid()
    twin_a1 = twin.commit(3, "arjuna", FIXED_LINE_3)
    assert twin_a1.hash != a1.hash


def test_crossing_to_nonexistent_strand_raises():
    b = Braid()
    b.commit(1, "krishna", FIXED_LINE)
    with pytest.raises(BraidError, match="unknown strand"):
        b.commit(2, "natasha", FIXED_LINE, crossings={"ghost": "0" * 64})


def test_crossing_to_self_raises():
    b = Braid()
    c1 = b.commit(1, "krishna", FIXED_LINE)
    with pytest.raises(BraidError, match="cannot cross itself"):
        b.commit(2, "krishna", FIXED_LINE, crossings={"krishna": c1.hash})


def test_stale_crossing_hash_raises():
    b = Braid()
    b.commit(1, "krishna", FIXED_LINE)
    stale = b.tip("krishna")
    b.commit(2, "krishna", FIXED_LINE_2)  # krishna advances; old tip is stale
    with pytest.raises(BraidError, match="stale or forged"):
        b.commit(3, "natasha", FIXED_LINE_3, crossings={"krishna": stale})
    with pytest.raises(BraidError, match="stale or forged"):
        b.commit(3, "natasha", FIXED_LINE_3, crossings={"krishna": "f" * 64})


# ---------------------------------------------------------------------------
# commit validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["", "Krishna", "HAS SPACE", "x" * 33, "dot.name", "plus+sign", "é", "a b"],
)
def test_bad_strand_names_raise(bad):
    b = Braid()
    with pytest.raises(BraidError):
        b.commit(1, bad, FIXED_LINE)


@pytest.mark.parametrize("ok", ["a", "x" * 32, "krishna-1_ok", "0", "_", "-"])
def test_valid_strand_names_accepted(ok):
    b = Braid()
    assert b.commit(1, ok, FIXED_LINE).strand == ok


@pytest.mark.parametrize("bad", [None, 123, b"bytes"])
def test_non_strand_types_raise(bad):
    b = Braid()
    with pytest.raises(BraidError):
        b.commit(1, bad, FIXED_LINE)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not a bus line",
        "[ 0123 | EXEC | #AAAAAAAAAAAAAAAAAAAAAA ]",           # ptr too short
        "[ 0123456789ABCDEF | EXEC | #AAAAAAAAAAAAAAAAAAAAAA ]",  # ptr not lowercase
        "[ 0123456789abcdef | exec | #AAAAAAAAAAAAAAAAAAAAAA ]",  # verb not uppercase
        "[0123456789abcdef | EXEC | #AAAAAAAAAAAAAAAAAAAAAA ]",   # missing space
        "[ 0123456789abcdef | EXEC | #AAAAAAAAAAAAAAAAAAAAA ]",   # tag 21 chars
        "[ 0123456789abcdef | EXEC | #AAAAAAAAAAAAAAAAAAAAAA ] ",  # trailing junk
        "[ 0123456789abcdef | EXEC | AAAAAAAAAAAAAAAAAAAAAA ]",   # missing '#'
    ],
)
def test_malformed_bus_line_raises(bad):
    b = Braid()
    with pytest.raises(BraidError):
        b.commit(1, "krishna", bad)


def test_braid_error_is_a_value_error():
    assert issubclass(BraidError, ValueError)
    with pytest.raises(ValueError):
        Braid().commit(1, "BAD STRAND", FIXED_LINE)


def test_strands_and_to_list_insertion_order():
    b = _woven_braid()
    assert b.strands() == ["krishna", "natasha", "arjuna"]
    seqs = [c.seq for c in b.to_list()]
    assert seqs == [1, 2, 3, 4]
    out = b.to_list()
    out.clear()  # returned list is a copy
    assert len(b.to_list()) == 4


# ---------------------------------------------------------------------------
# from_events rebuild + tamper checks
# ---------------------------------------------------------------------------


def test_from_events_roundtrip_matches_original_root():
    b = _woven_braid()
    rebuilt = Braid.from_events(_events_of(b))
    assert rebuilt.root() == b.root()
    assert [asdict(c) for c in rebuilt.to_list()] == [asdict(c) for c in b.to_list()]


def test_from_events_skips_non_braid_events_and_accepts_bare_records():
    b = _woven_braid()
    events = [{"type": "fsm.transition", "payload": {"event": "E3"}, "seq": 99}]
    events += _events_of(b)[:2]
    events.append({"seq": 100, "type": "instruction.rejected", "payload": {}})
    # bare commitment record (fields at top level, no "payload" wrapper)
    bare = {"type": "braid.commit", **asdict(b.to_list()[2])}
    events.append(bare)
    events += _events_of(b)[3:]
    assert Braid.from_events(events).root() == b.root()


def test_from_events_empty_list_matches_fresh_braid():
    assert Braid.from_events([]).root() == Braid().root()
    assert Braid.from_events([{"type": "fsm.transition"}]).strands() == []


def test_from_events_tampered_hash_raises():
    events = _events_of(_woven_braid())
    payload = dict(events[1]["payload"])
    payload["hash"] = ("0" if payload["hash"][0] != "0" else "1") + payload["hash"][1:]
    events[1] = {**events[1], "payload": payload}
    with pytest.raises(BraidError, match="tampered commitment"):
        Braid.from_events(events)


def test_from_events_tampered_bus_line_raises():
    events = _events_of(_woven_braid())
    payload = dict(events[0]["payload"])
    payload["bus_line"] = FIXED_LINE_3  # hash no longer covers the line
    events[0] = {**events[0], "payload": payload}
    with pytest.raises(BraidError, match="tampered commitment"):
        Braid.from_events(events)


def test_from_events_tampered_prev_with_recomputed_hash_raises():
    b = Braid()
    b.commit(1, "krishna", FIXED_LINE)
    b.commit(2, "krishna", FIXED_LINE_2)
    events = _events_of(b)
    payload = dict(events[1]["payload"])
    payload["prev"] = "0" * 64  # attacker re-links to genesis...
    payload["hash"] = hashlib.sha256(  # ...and honestly re-hashes the forgery
        canonical_json(
            {
                "seq": payload["seq"],
                "strand": payload["strand"],
                "bus_line": payload["bus_line"],
                "prev": payload["prev"],
                "crossings": payload["crossings"],
            }
        ).encode("utf-8")
    ).hexdigest()
    events[1] = {**events[1], "payload": payload}
    with pytest.raises(BraidError, match="broken chain linkage"):
        Braid.from_events(events)


def test_from_events_stale_crossing_with_recomputed_hash_raises():
    b = Braid()
    b.commit(1, "krishna", FIXED_LINE)
    b.commit(2, "krishna", FIXED_LINE_2)
    stale = b.to_list()[0].hash
    forged = {
        "seq": 3,
        "strand": "natasha",
        "bus_line": FIXED_LINE_3,
        "prev": "0" * 64,
        "crossings": {"krishna": stale},  # not krishna's current tip
    }
    forged["hash"] = hashlib.sha256(canonical_json(forged).encode("utf-8")).hexdigest()
    events = _events_of(b) + [
        {"type": "braid.commit", "payload": forged, "seq": 3}
    ]
    with pytest.raises(BraidError, match="stale or forged"):
        Braid.from_events(events)


@pytest.mark.parametrize(
    "key,bad_value",
    [
        ("hash", "Z" * 64),          # non-hex declared hash
        ("hash", "abcd"),            # short declared hash
        ("prev", "0" * 63),          # short prev
        ("strand", "BAD NAME"),
        ("seq", -1),
        ("seq", "1"),
        ("bus_line", "junk"),
    ],
)
def test_from_events_malformed_fields_raise(key, bad_value):
    events = _events_of(_woven_braid())
    payload = dict(events[0]["payload"])
    payload[key] = bad_value
    events[0] = {**events[0], "payload": payload}
    with pytest.raises(BraidError):
        Braid.from_events(events)


def test_from_events_missing_field_raises():
    events = _events_of(_woven_braid())
    payload = dict(events[0]["payload"])
    del payload["hash"]
    events[0] = {**events[0], "payload": payload}
    with pytest.raises(BraidError, match="missing"):
        Braid.from_events(events)


# ---------------------------------------------------------------------------
# sign_root / verify_root_signature (domain-separated, ADR-004 dual mode)
# ---------------------------------------------------------------------------


def test_sign_verify_root_roundtrip_hmac(hmac_signer, hmac_verifier):
    root = _woven_braid().root()
    sig = sign_root(root, hmac_signer)
    assert isinstance(sig, str) and len(sig) == 22  # HMAC mode: 16-byte tag
    assert verify_root_signature(root, sig, hmac_verifier) is True


def test_sign_verify_root_roundtrip_ed25519(ed_signer, ed_verifier):
    root = _woven_braid().root()
    sig = sign_root(root, ed_signer)
    assert isinstance(sig, str) and len(sig) == 86  # Ed25519 mode: full 64-byte sig
    assert verify_root_signature(root, sig, ed_verifier) is True


def test_sign_root_binds_domain_separator(hmac_signer):
    root = hashlib.sha256(b"some braid state").hexdigest()
    sig = sign_root(root, hmac_signer)
    import hmac as _hmac

    expected = _hmac.new(
        HMAC_KEY, DOMAIN_SEPARATOR + bytes.fromhex(root), hashlib.sha256
    ).digest()[:16]
    import base64

    assert sig == base64.urlsafe_b64encode(expected).decode("ascii").rstrip("=")
    assert DOMAIN_SEPARATOR == b"HELIX-BRAID/1"


def test_verify_root_signature_wrong_key(hmac_signer, ed_signer):
    root = _woven_braid().root()
    assert verify_root_signature(root, sign_root(root, hmac_signer), HMACVerifier(WRONG_HMAC_KEY)) is False
    wrong_ed = Ed25519Verifier(Ed25519Signer(WRONG_ED_SEED).public_key_bytes())
    assert verify_root_signature(root, sign_root(root, ed_signer), wrong_ed) is False


def test_verify_root_signature_tampered_root(hmac_signer, hmac_verifier):
    sig = sign_root(_woven_braid().root(), hmac_signer)
    other_root = Braid().root()  # different, well-formed root
    assert verify_root_signature(other_root, sig, hmac_verifier) is False


def test_verify_root_signature_fail_closed_on_garbage(hmac_verifier):
    root = _woven_braid().root()
    assert verify_root_signature(root, "not base64url!!!", hmac_verifier) is False
    assert verify_root_signature(root, "AAAA", hmac_verifier) is False  # wrong length
    assert verify_root_signature(root, None, hmac_verifier) is False
    assert verify_root_signature("not-a-root", "A" * 22, hmac_verifier) is False


# ---------------------------------------------------------------------------
# anchor_braid / verify_anchor end-to-end against SimBackend
# ---------------------------------------------------------------------------


def _anchored_braid(signer, backend) -> Braid:
    b = Braid()
    b.commit(1, "krishna", _bus_line(signer, backend, "boot kernel"))
    b.commit(2, "natasha", _bus_line(signer, backend, "sync memory"))
    b.commit(3, "krishna", _bus_line(signer, backend, "tick fsm"),
             crossings={"natasha": b.tip("natasha")})
    return b


def test_anchor_braid_end_to_end(hmac_signer, hmac_verifier, backend):
    braid = _anchored_braid(hmac_signer, backend)
    root = braid.root()
    anchor = anchor_braid(braid, hmac_signer, backend, 1, 3)
    match = W.BUS_RE.fullmatch(anchor)
    assert match is not None
    assert match.group("verb") == "ARCHIVE"
    # the anchor is an ordinary verifiable triangulated instruction
    assert W.verify_instruction_lock(anchor, hmac_verifier, backend=backend) is True
    # stored envelope body carries the anchor payload
    body = json.loads(json.loads(backend.fetch_payload(match.group("ptr")))["body"])
    assert body["braid_root"] == root
    assert body["seq_lo"] == 1 and body["seq_hi"] == 3
    assert body["strands"] == 2
    assert isinstance(body["ts"], int)
    # full semantic check on a fresh anchor (replay cache: one verify per nonce)
    fresh = anchor_braid(braid, hmac_signer, backend, 1, 3)
    assert verify_anchor(fresh, hmac_verifier, root, backend) is True


def test_anchor_braid_end_to_end_ed25519(ed_signer, ed_verifier, backend):
    braid = _anchored_braid(ed_signer, backend)
    anchor = anchor_braid(braid, ed_signer, backend, 1, 3)
    assert len(W.BUS_RE.fullmatch(anchor).group("tag")) == 86
    assert verify_anchor(anchor, ed_verifier, braid.root(), backend) is True


def test_verify_anchor_false_with_wrong_root(hmac_signer, hmac_verifier, backend):
    braid = _anchored_braid(hmac_signer, backend)
    anchor = anchor_braid(braid, hmac_signer, backend, 1, 3)
    assert verify_anchor(anchor, hmac_verifier, "0" * 64, backend) is False
    # and a fresh anchor fails against a different braid's root
    other = Braid()
    other.commit(1, "krishna", _bus_line(hmac_signer, backend, "different state"))
    fresh = anchor_braid(braid, hmac_signer, backend, 1, 3)
    assert verify_anchor(fresh, hmac_verifier, other.root(), backend) is False


def test_verify_anchor_false_with_wrong_key(hmac_signer, backend):
    braid = _anchored_braid(hmac_signer, backend)
    anchor = anchor_braid(braid, hmac_signer, backend, 1, 3)
    assert verify_anchor(anchor, HMACVerifier(WRONG_HMAC_KEY), braid.root(), backend) is False


def test_verify_anchor_fail_closed_on_garbage(hmac_verifier, backend):
    root = _woven_braid().root()
    assert verify_anchor("not an anchor line", hmac_verifier, root, backend) is False
    assert verify_anchor(FIXED_LINE, hmac_verifier, root, backend) is False  # no such ptr
    assert verify_anchor(None, hmac_verifier, root, backend) is False


def test_anchor_forward_progress(hmac_signer, hmac_verifier, backend):
    braid = _anchored_braid(hmac_signer, backend)
    root1 = braid.root()
    anchor1 = anchor_braid(braid, hmac_signer, backend, 1, 3)
    # more commits -> root moves -> a new anchor differs from the old one
    braid.commit(4, "natasha", _bus_line(hmac_signer, backend, "phase two"))
    root2 = braid.root()
    assert root2 != root1
    anchor2 = anchor_braid(braid, hmac_signer, backend, 1, 4)
    assert anchor2 != anchor1
    assert verify_anchor(anchor2, hmac_verifier, root2, backend) is True
    # the old root no longer matches anchors of the advanced braid
    anchor3 = anchor_braid(braid, hmac_signer, backend, 1, 4)
    assert verify_anchor(anchor3, hmac_verifier, root1, backend) is False


def test_anchor_braid_validates_range(hmac_signer, backend):
    braid = Braid()
    with pytest.raises(ValueError):
        anchor_braid(braid, hmac_signer, backend, 5, 3)
    with pytest.raises(ValueError):
        anchor_braid(braid, hmac_signer, backend, -1, 3)


# ---------------------------------------------------------------------------
# thread safety
# ---------------------------------------------------------------------------


def test_concurrent_commits_are_serialized():
    b = Braid()
    errors: list[BaseException] = []

    def worker(name: str) -> None:
        try:
            for i in range(25):
                b.commit(i, name, FIXED_LINE)
        except BaseException as exc:  # noqa: BLE001 - collected and asserted
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(f"agent{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(b.to_list()) == 100
    assert sorted(b.strands()) == [f"agent{i}" for i in range(4)]
    for strand in b.strands():
        commits = [c for c in b.to_list() if c.strand == strand]
        assert len(commits) == 25
        assert commits[0].prev == GENESIS_TIP
        for earlier, later in zip(commits, commits[1:]):
            assert later.prev == earlier.hash
    # the interleaved journal replays to the same root
    assert Braid.from_events(_events_of(b)).root() == b.root()
