"""AKASH braid — verifiable AKASH braid signatures over the triangulated bus (SPEC-M1 §1).

Defines the **AKASH braid signature**: a set of named strands (one per
agent/writer), each a hash-chained sequence of :class:`Commitment` records,
optionally woven together by **crossings** — references to other strands' tips
at commit time that establish a causal/topological order across strands. The
**braid root** (a single sha256 digest over all strand tips) is the
integrity anchor for an epoch of journaled instructions: it is signed
directly (:func:`sign_root`, domain-separated) and periodically anchored onto
the bus itself (:func:`anchor_braid`) as an ``ARCHIVE`` instruction whose
stored envelope carries the root — the "AKASH braid signature anchor".

Verifiable AKASH braid signatures are the M1 deliverable (SPEC-M1 preamble)
and build directly on the M0 signing model:

- ADR-004 (pointer model + dual-mode signing, docs/adr/ADR-004): every anchor
  is an ordinary triangulated bus line, so it inherits write-once 64-bit
  slots, generation counters, and HMAC-dev / Ed25519-prod tags.
- Audit AUD-C6 / AUD-H1 (HELIXos_Handoff_Audit): symmetric-forgery and
  truncated-tag findings — root signatures reuse ``signers.py`` tag codecs
  unchanged, so a braid root can be verified with only the public key.
- ADR-001 (journal is the source of truth): the braid is a pure function of
  the journal — :meth:`Braid.from_events` rebuilds it from ``braid.commit``
  events, recomputing and validating EVERY hash (the tamper check), so a
  recomputed root can be checked against any anchored root.

Determinism: commitments carry NO timestamps — the same event list always
rebuilds the same root. Thread-safe: commits serialize on one lock.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import threading
import time
from dataclasses import dataclass

from KNOT_API_WRAPPER import (
    BUS_RE,
    generate_triangulated_instruction,
    verify_instruction_lock,
)

log = logging.getLogger("helix.akash.braid")

__all__ = [
    "BraidError",
    "Commitment",
    "Braid",
    "canonical_json",
    "sign_root",
    "verify_root_signature",
    "anchor_braid",
    "verify_anchor",
    "DOMAIN_SEPARATOR",
    "GENESIS_TIP",
]

# Domain separator for root signatures (SPEC-M1 §1): signatures bind
# b"HELIX-BRAID/1" || bytes.fromhex(root) so a braid-root signature can never
# be confused with a bus tag or any other message.
DOMAIN_SEPARATOR = b"HELIX-BRAID/1"

# Tip hash of a strand with no commitments yet (SPEC-M1 §1: genesis "0"*64).
GENESIS_TIP = "0" * 64

_STRAND_RE = re.compile(r"[a-z0-9_-]{1,32}")
_HEX64_RE = re.compile(r"[0-9a-f]{64}")


class BraidError(ValueError):
    """Invalid strand name, bus line, crossing, or broken hash chain (SPEC-M1 §1)."""


def canonical_json(obj) -> str:
    """Canonical JSON encoding (SPEC-M1 §1): sorted keys, compact separators."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _commitment_hash(seq: int, strand: str, bus_line: str, prev: str, crossings: dict) -> str:
    """sha256 hex of canonical_json({seq,strand,bus_line,prev,crossings})."""
    return hashlib.sha256(
        canonical_json(
            {
                "seq": seq,
                "strand": strand,
                "bus_line": bus_line,
                "prev": prev,
                "crossings": crossings,
            }
        ).encode("utf-8")
    ).hexdigest()


def _check_strand(strand: str) -> str:
    if not isinstance(strand, str) or not _STRAND_RE.fullmatch(strand):
        raise BraidError(
            f"strand must fullmatch [a-z0-9_-]{{1,32}}, got {strand!r}"
        )
    return strand


def _check_seq(seq: int) -> int:
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
        raise BraidError(f"seq must be a non-negative int, got {seq!r}")
    return seq


def _check_bus_line(bus_line: str) -> str:
    if not isinstance(bus_line, str) or not BUS_RE.fullmatch(bus_line):
        raise BraidError(f"bus_line does not fullmatch BUS_RE: {bus_line!r}")
    return bus_line


def _check_hex64(value: str, what: str) -> str:
    if not isinstance(value, str) or not _HEX64_RE.fullmatch(value):
        raise BraidError(f"{what} must be 64 lowercase hex chars, got {value!r}")
    return value


@dataclass(frozen=True)
class Commitment:
    """One journaled instruction woven into a strand (SPEC-M1 §1)."""

    seq: int                 # global journal seq this commitment corresponds to
    strand: str              # e.g. "krishna", "natasha"
    bus_line: str            # the triangulated instruction committed
    prev: str                # previous tip hash of THIS strand (genesis: "0"*64)
    crossings: dict[str, str]  # {other_strand: tip_hash_at_commit_time}, sorted keys, may be {}
    hash: str                # sha256 hex of canonical_json({seq,strand,bus_line,prev,crossings})


class Braid:
    """A set of named hash-chained strands woven by crossings (SPEC-M1 §1)."""

    def __init__(self) -> None:
        self._tips: dict[str, str] = {}        # strand -> current tip hash (insertion-ordered)
        self._commitments: list[Commitment] = []  # insertion order
        self._lock = threading.Lock()

    def commit(
        self,
        seq: int,
        strand: str,
        bus_line: str,
        crossings: dict[str, str] | None = None,
    ) -> Commitment:
        """Append a commitment to ``strand``; return it.

        Validates (BraidError on any violation):
        - ``strand`` fullmatches ``[a-z0-9_-]{1,32}``;
        - ``bus_line`` fullmatches ``BUS_RE`` (the triangulated wire format);
        - every crossing references an EXISTING strand other than ``strand``
        and its declared hash equals that strand's CURRENT tip (a stale tip
        means the declared causal order no longer holds).
        ``prev`` is by construction the strand's current tip (genesis "0"*64).
        """
        _check_seq(seq)
        _check_strand(strand)
        _check_bus_line(bus_line)
        if crossings is None:
            crossings = {}
        if not isinstance(crossings, dict):
            raise BraidError(f"crossings must be a dict, got {type(crossings).__name__}")
        with self._lock:
            checked: dict[str, str] = {}
            for name, declared in crossings.items():
                if not isinstance(name, str):
                    raise BraidError(f"crossing strand name must be str, got {name!r}")
                if name == strand:
                    raise BraidError(f"strand {strand!r} cannot cross itself")
                if name not in self._tips:
                    raise BraidError(f"crossing references unknown strand {name!r}")
                if not isinstance(declared, str) or declared != self._tips[name]:
                    raise BraidError(
                        f"crossing to {name!r} declares {declared!r} but current tip is "
                        f"{self._tips[name]!r} (stale or forged causal reference)"
                    )
                checked[name] = declared
            checked = dict(sorted(checked.items()))  # sorted keys, per SPEC-M1 §1
            prev = self._tips.get(strand, GENESIS_TIP)  # prev == current tip
            digest = _commitment_hash(seq, strand, bus_line, prev, checked)
            commitment = Commitment(
                seq=seq,
                strand=strand,
                bus_line=bus_line,
                prev=prev,
                crossings=checked,
                hash=digest,
            )
            self._tips[strand] = digest
            self._commitments.append(commitment)
            return commitment

    def tip(self, strand: str) -> str:
        """Current tip hash of ``strand`` ("0"*64 if the strand is new)."""
        with self._lock:
            return self._tips.get(strand, GENESIS_TIP)

    def strands(self) -> list[str]:
        """Strand names in order of first commitment."""
        with self._lock:
            return list(self._tips)

    def root(self) -> str:
        """sha256 hex of canonical_json({strand: tip}, sorted) over all strands."""
        with self._lock:
            snapshot = dict(self._tips)
        return hashlib.sha256(canonical_json(snapshot).encode("utf-8")).hexdigest()

    def to_list(self) -> list[Commitment]:
        """All commitments in insertion order."""
        with self._lock:
            return list(self._commitments)

    @classmethod
    def from_events(cls, events: list[dict]) -> "Braid":
        """Rebuild a braid from journaled ``braid.commit`` events (SPEC-M1 §1).

        Accepts journal line dicts (commitment fields under ``payload``) or
        bare commitment records carrying ``type: "braid.commit"``; events of
        any other type are skipped. Every declared field is re-validated and
        every hash recomputed — the declared commitment hash must equal the
        recomputed one AND the recomputed chain (``prev`` == rebuilt tip,
        crossings == rebuilt tips) must reproduce it — any mismatch raises
        BraidError. This is the tamper check behind SPEC-M1 §6(a): the braid
        root is recomputable from the journal alone, and only from an
        untampered journal.
        """
        braid = cls()
        for index, event in enumerate(events):
            if not isinstance(event, dict) or event.get("type") != "braid.commit":
                continue
            if "payload" in event:
                fields = event["payload"]
                if not isinstance(fields, dict):
                    raise BraidError(
                        f"braid.commit event #{index}: payload must be a dict, "
                        f"got {type(fields).__name__}"
                    )
            else:
                fields = event  # bare commitment record with type at top level
            try:
                seq = fields["seq"]
                strand = fields["strand"]
                bus_line = fields["bus_line"]
                prev = fields["prev"]
                crossings = fields.get("crossings", {})
                declared = fields["hash"]
            except (KeyError, TypeError) as exc:
                raise BraidError(
                    f"braid.commit event #{index}: missing or unreadable field: {exc}"
                ) from exc
            what = f"braid.commit event #{index}"
            _check_seq(seq)
            _check_strand(strand)
            _check_bus_line(bus_line)
            _check_hex64(prev, f"{what}: prev")
            _check_hex64(declared, f"{what}: hash")
            if not isinstance(crossings, dict):
                raise BraidError(f"{what}: crossings must be a dict")
            for name, tip_at_commit in crossings.items():
                _check_strand(name)
                _check_hex64(tip_at_commit, f"{what}: crossings[{name!r}]")
            # Tamper check 1: the declared hash must be the recomputed hash of
            # the declared fields.
            recomputed = _commitment_hash(seq, strand, bus_line, prev, crossings)
            if not hmac.compare_digest(declared, recomputed):
                raise BraidError(
                    f"{what}: declared hash does not match recomputed commitment hash "
                    f"(tampered commitment)"
                )
            # Tamper check 2 + chain rebuild: commit() enforces crossing
            # validity and links prev to the rebuilt tip; its recomputed hash
            # covers prev, so equality holds iff prev == the rebuilt tip.
            commitment = braid.commit(seq, strand, bus_line, crossings)
            if not hmac.compare_digest(commitment.hash, declared):
                raise BraidError(
                    f"{what}: prev {prev!r} does not match the rebuilt strand tip "
                    f"(broken chain linkage)"
                )
        return braid


def _root_message(root: str) -> bytes:
    if not isinstance(root, str) or not _HEX64_RE.fullmatch(root):
        raise ValueError(f"root must be 64 lowercase hex chars, got {root!r}")
    return DOMAIN_SEPARATOR + bytes.fromhex(root)


def sign_root(root: str, signer) -> str:
    """b64url signature over b"HELIX-BRAID/1" || bytes.fromhex(root) (SPEC-M1 §1).

    Works with either signer mode from signers.py (HMAC-dev, Ed25519-prod);
    the wire form is the mode's canonical tag encoding via ``b64tag``.
    """
    if not (hasattr(signer, "sign") and hasattr(signer, "b64tag")):
        raise ValueError("signer must expose sign()/b64tag() — see signers.py")
    return signer.b64tag(signer.sign(_root_message(root)))


def verify_root_signature(root: str, sig_b64: str, verifier) -> bool:
    """Fail-closed check of a :func:`sign_root` signature; False on ANY error."""
    try:
        if not isinstance(sig_b64, str):
            return False
        message = _root_message(root)
        tag = verifier.tag_from_b64(sig_b64)  # validates charset AND length per mode
        return bool(verifier.verify(message, tag))
    except Exception:
        log.exception("verify_root_signature: failing closed")
        return False


def anchor_braid(braid: Braid, signer, backend, seq_lo: int, seq_hi: int) -> str:
    """Anchor the current braid root on the bus; return the anchor bus line.

    The payload is compact JSON
    ``{"braid_root","seq_lo","seq_hi","strands","ts"}`` wrapped via
    ``generate_triangulated_instruction(payload, "ARCHIVE", signer, backend)``
    — an ordinary triangulated instruction, verifiable by the standard lock
    (ADR-004), carrying the braid root as the "AKASH braid signature anchor".
    """
    if not isinstance(braid, Braid):
        raise ValueError(f"braid must be a Braid, got {type(braid).__name__}")
    for name, value in (("seq_lo", seq_lo), ("seq_hi", seq_hi)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative int, got {value!r}")
    if seq_lo > seq_hi:
        raise ValueError(f"seq_lo {seq_lo} > seq_hi {seq_hi}")
    payload = canonical_json(
        {
            "braid_root": braid.root(),
            "seq_lo": seq_lo,
            "seq_hi": seq_hi,
            "strands": len(braid.strands()),
            "ts": int(time.time()),
        }
    )
    return generate_triangulated_instruction(payload, "ARCHIVE", signer, backend)


def verify_anchor(
    anchor_line: str,
    verifier,
    expected_root: str,
    backend,
    expected_phase: int = 0,
) -> bool:
    """Fail-closed anchor check (SPEC-M1 §1).

    True iff (a) ``verify_instruction_lock`` passes on the anchor line (tag,
    freshness, replay, cauldron phase) AND (b) the decoded stored envelope's
    body JSON has ``braid_root == expected_root``. Never raises.
    """
    try:
        if backend is None:
            raise ValueError("backend is required to decode the anchor envelope")
        if not isinstance(expected_root, str):
            return False
        if not verify_instruction_lock(anchor_line, verifier, expected_phase, backend):
            return False
        match = BUS_RE.fullmatch(anchor_line.strip()) if isinstance(anchor_line, str) else None
        if match is None:
            return False
        envelope = backend.fetch_payload(match.group("ptr"))
        if not isinstance(envelope, str):
            return False
        body = json.loads(envelope).get("body")
        if not isinstance(body, str):
            return False
        claimed_root = json.loads(body).get("braid_root")
        if not isinstance(claimed_root, str):
            return False
        return hmac.compare_digest(claimed_root, expected_root)
    except Exception:
        log.exception("verify_anchor: failing closed")
        return False
