"""HELIXos FSM executor — bridges the triangulated bus to the TEN-SQUARED FSM.

SPEC-M1 §3 contract; fixes audit finding AUD-C8 by wiring the runtime to the
collapse/recovery protocol of ADR-001 §2.3:

    collapse -> increment epoch (journaled ``epoch.collapse``) -> rebuild
    from snapshot + journal replay -> fence stale epochs.

* ``apply`` runs the six-step pipeline of SPEC-M1 §3: (1) epoch fence match,
  (2) fail-closed ``verify_instruction_lock``, (3) event derivation via
  :func:`event_for`, (4) FSM transition journaled as ``fsm.transition``,
  (5) optional braid commitment journaled as ``braid.commit``, (6) True.
  It NEVER raises on the rejection path: bad/forged/replayed/stale
  instructions are journaled ``instruction.rejected`` (with ``reason`` one of
  ``invalid|stale|replay|phase``) and return False with no state change.
* Backend Unavailable (sidecar down, ``sidecar_client.Unavailable``) is
  journaled ``backend.unavailable`` and returns False — NO exception, NO
  state change (the M1 chaos test relies on this).  Because
  ``verify_instruction_lock`` swallows backend exceptions into a bare False,
  the executor probes ``fetch_payload`` itself first so transport failure is
  classified as unavailability rather than rejection.
* ``collapse(reason)`` is the ADR-001 §2.3 trigger: ``EpochFence.increment``
  first, then the ``epoch.collapse`` journal event written under the NEW
  epoch; the executor then operates at the new epoch.
* ``recover`` is ADR-001 §2.3 step 2: optional snapshot load (fail-closed
  validation), chain-verified journal replay since the snapshot seq,
  re-application of ``fsm.transition`` events only, and — when a braid
  instance is supplied — re-commit of journaled ``braid.commit`` events.

Phase policy (SPEC-M1 §5, binding): slot generations bump ONLY via
``knotcore_sim.bump_generation`` (or the sidecar ABI equivalent), invoked by
the anchor cadence process — after each braid anchor — and on EVERY collapse
for all live ptrs.  The executor deliberately does NOT bump generations: it
holds no pointer inventory and the ``KnotBackend`` protocol exposes no bump
operation.  Its role is the enforcement side: post-bump, instructions signed
under the old phase fail ``verify_cauldron_phase`` and are journaled
``instruction.rejected`` with ``reason="phase"`` — intended forward security,
documented here so the collapse/anchor orchestrator's duty is unmissable.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from pathlib import Path
from typing import Callable

import KNOT_API_WRAPPER as knot
from epochs import EpochFence
from journal import EventJournal
from sidecar_client import Unavailable
from snapshot import load_snapshot, replay_events
from ten_squared_fsm import TenSquaredFSM

log = logging.getLogger("helix.runtime.executor")

__all__ = ["FSMExecutor", "event_for"]

# Envelope-body override: {"event":"E0".."E9"} — exactly one digit (SPEC-M1 §3.3).
_EVENT_OVERRIDE_RE = re.compile(r"^E[0-9]$")


def _default_audit(record: str) -> None:
    log.info("audit %s", record)


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def event_for(bus_line: str, body: str | None = None) -> str:
    """Map a bus line to its FSM event ("E0".."E9").  Pure; exported for tests.

    Override first (SPEC-M1 §3 step 3): if ``body`` — the already-fetched,
    already-verified envelope *body* text, passed in by the caller so this
    function stays I/O-free — parses as JSON with an ``"event"`` member
    matching ``E[0-9]``, that event wins.

    Otherwise deterministic derivation: base64url-decode the bus-line tag
    (its charset and length are already pinned by ``BUS_RE``) and take the
    first byte mod 10.  This is exactly SPEC-M1 §3's
    ``int(tag bytes hex[:2], 16) % 10``: the first two hex digits of the
    decoded tag ARE the first byte.  Choice documented: we decode the wire
    tag rather than re-reading the store, keeping the mapping a pure function
    of the bus line itself.

    Raises ValueError (dev-visible) if ``bus_line`` is not a well-formed
    triangulated instruction.
    """
    if not isinstance(bus_line, str):
        raise ValueError(f"bus_line must be str, got {type(bus_line).__name__}")
    m = knot.BUS_RE.fullmatch(bus_line.strip())
    if m is None:
        raise ValueError(f"not a triangulated bus line: {bus_line!r}")
    if body is not None:
        payload = None
        if isinstance(body, str):
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                payload = None
        if isinstance(payload, dict):
            override = payload.get("event")
            if isinstance(override, str) and _EVENT_OVERRIDE_RE.fullmatch(override):
                return override
    tag_raw = _b64url_decode(m.group("tag"))
    return f"E{tag_raw[0] % 10}"


class FSMExecutor:
    """SPEC-M1 §3 executor: bus line -> verify -> TEN-SQUARED transition -> journal -> braid.

    The executor owns one epoch (captured from ``fence`` at construction and
    advanced only by :meth:`collapse`).  Every journaled event carries that
    epoch; if the fence has moved on (another node collapsed first) the
    executor is stale and ``apply`` refuses — ADR-001 §2.3 step 4 split-brain
    defense.  ``audit`` is a ``Callable[[str], None]`` like possession.py's.
    """

    def __init__(
        self,
        fsm: TenSquaredFSM,
        journal: EventJournal,
        fence: EpochFence,
        verifier,
        backend,
        braid=None,
        audit: Callable[[str], None] | None = None,
    ) -> None:
        self._fsm = fsm
        self._journal = journal
        self._fence = fence
        self._verifier = verifier
        self._backend = backend
        self._braid = braid
        self._audit: Callable[[str], None] = audit if audit is not None else _default_audit
        self._epoch = fence.current

    # ------------------------------------------------------------ introspection
    @property
    def fsm(self) -> TenSquaredFSM:
        return self._fsm

    @property
    def journal(self) -> EventJournal:
        return self._journal

    @property
    def epoch(self) -> int:
        """The epoch this executor journals under (advanced by collapse only)."""
        return self._epoch

    # ------------------------------------------------------------------- apply
    def apply(self, bus_line: str, strand: str = "kernel", expected_phase: int = 0) -> bool:
        """Run the SPEC-M1 §3 six-step pipeline.  Never raises on rejection paths."""
        # 1. Epoch fence: this executor's journaled epoch must match the live
        #    fence, else it is stale post-collapse state — refuse (audit only;
        #    journaling under a stale epoch is exactly what the fence forbids).
        current = self._fence.current
        if current != self._epoch:
            self._audit(
                f"fsm.apply.refused stale_epoch executor={self._epoch} fence={current}"
            )
            log.warning(
                "apply refused: executor epoch %d fenced by live epoch %d",
                self._epoch,
                current,
            )
            return False

        # 2. Verify, fail-closed.  Probe fetch_payload first: the wrapper's
        #    verify swallows backend exceptions into False, so without the
        #    probe a dead sidecar would be misclassified as a rejection.
        match = knot.BUS_RE.fullmatch(bus_line.strip()) if isinstance(bus_line, str) else None
        envelope: str | None = None
        if match is not None:
            try:
                envelope = self._backend.fetch_payload(match.group("ptr"))
            except Unavailable as exc:
                self._journal.append(
                    "backend.unavailable",
                    {"error": str(exc), "ptr": match.group("ptr")},
                    epoch=self._epoch,
                )
                self._audit(f"backend.unavailable error={exc}")
                return False
        if not knot.verify_instruction_lock(
            bus_line, self._verifier, expected_phase, self._backend
        ):
            try:
                reason = self._classify_rejection(match, envelope, expected_phase)
            except Unavailable as exc:  # backend died between probe and classify
                self._journal.append(
                    "backend.unavailable", {"error": str(exc)}, epoch=self._epoch
                )
                self._audit(f"backend.unavailable error={exc}")
                return False
            except Exception:  # classifier is best-effort; never let it raise
                log.exception("rejection classifier failed; defaulting to 'invalid'")
                reason = "invalid"
            self._journal.append(
                "instruction.rejected",
                {
                    # payload must stay JSON-serializable even for garbage input
                    "bus_line": bus_line if isinstance(bus_line, str) else repr(bus_line),
                    "reason": reason,
                    "strand": strand,
                },
                epoch=self._epoch,
            )
            self._audit(f"instruction.rejected reason={reason} strand={strand}")
            return False

        # 3. Event: envelope-body {"event":"E[0-9]"} override, else tag-derived.
        body: str | None = None
        if envelope is not None:
            try:
                candidate = json.loads(envelope).get("body")
            except (json.JSONDecodeError, AttributeError):
                candidate = None
            if isinstance(candidate, str):
                body = candidate
        event = event_for(bus_line, body)

        # 4. Transition, then journal it (the journal is the system of record).
        from_state = self._fsm.state
        to_state = self._fsm.transition(event)
        seq = self._journal.append(
            "fsm.transition",
            {
                "bus_line": bus_line,
                "event": event,
                "from": from_state,
                "to": to_state,
                "strand": strand,
            },
            epoch=self._epoch,
        )

        # 5. Optional braid commitment (duck-typed per SPEC-M1 §1), journaled
        #    with everything needed to re-commit on recovery.
        if self._braid is not None:
            commitment = self._braid.commit(seq, strand, bus_line)
            root = self._braid.root() if hasattr(self._braid, "root") else None
            self._journal.append(
                "braid.commit",
                {
                    "seq": seq,
                    "strand": strand,
                    "bus_line": bus_line,
                    "crossings": dict(getattr(commitment, "crossings", None) or {}),
                    "hash": getattr(commitment, "hash", None),
                    "root": root,
                },
                epoch=self._epoch,
            )

        # 6. Done.
        self._audit(
            f"fsm.transition event={event} from={from_state} to={to_state} seq={seq}"
        )
        return True

    def _classify_rejection(
        self, match, envelope: str | None, expected_phase: int
    ) -> str:
        """Best-effort post-mortem of a False verify -> invalid|stale|replay|phase.

        Re-walks verify_instruction_lock's checks in its own order so the
        journaled reason names the first failing stage.  ``replay`` is the
        residual: an instruction that is well-formed, fresh, correctly signed
        and phase-valid yet still rejected has had its nonce seen before.
        """
        if match is None:
            return "invalid"
        ptr, verb, tag_text = match.group("ptr"), match.group("verb"), match.group("tag")
        if verb not in knot.ALLOWED_VERBS:
            return "invalid"
        if envelope is None or not isinstance(envelope, str):
            return "invalid"  # unknown ptr / nothing stored
        try:
            env = json.loads(envelope)
            ts = int(env["ts"])
            if not isinstance(env.get("body"), str):
                return "invalid"
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return "invalid"
        if abs(time.time() - ts) > knot.MAX_CLOCK_SKEW_S:
            return "stale"
        try:
            tag = self._verifier.tag_from_b64(tag_text)
            if not self._verifier.verify(knot._canonical(ptr, verb, envelope), tag):
                return "invalid"  # forgery / wrong key / tampered axes
        except Exception:
            return "invalid"
        if not self._backend.verify_cauldron_phase(ptr, tag_text, int(expected_phase)):
            return "phase"  # SPEC-M1 §5: pre-anchor instruction re-presented post-bump
        return "replay"

    # ---------------------------------------------------------------- collapse
    def collapse(self, reason: str) -> int:
        """Projective Collapse trigger (ADR-001 §2.3 step 1); returns the new epoch.

        Order is binding: increment the fence FIRST, then journal
        ``epoch.collapse`` under the new epoch.  SPEC-M1 §5 duty of the
        caller: the collapse/anchor orchestrator MUST ``bump_generation``
        every live ptr at collapse (see module docstring); this executor
        enforces the consequence — old-phase instructions then verify False
        and are journaled ``instruction.rejected`` with reason ``phase``.
        """
        if not isinstance(reason, str) or not reason:
            raise ValueError("reason must be a non-empty str")
        new_epoch = self._fence.increment()
        self._epoch = new_epoch
        self._journal.append(
            "epoch.collapse",
            {"reason": reason, "new_epoch": new_epoch},
            epoch=new_epoch,
        )
        self._audit(f"epoch.collapse new_epoch={new_epoch} reason={reason!r}")
        log.info("epoch.collapse new_epoch=%d reason=%r", new_epoch, reason)
        return new_epoch

    # ---------------------------------------------------------------- recovery
    @classmethod
    def recover(
        cls,
        journal_path: str | Path,
        fence: EpochFence,
        verifier,
        backend,
        snapshot_path: str | Path | None = None,
        braid=None,
    ) -> "FSMExecutor":
        """Rebuild an executor from snapshot + journal (ADR-001 §2.3 step 2).

        Fail-closed: a tampered snapshot raises SnapshotError; the journal
        hash chain is verified BEFORE anything is replayed and a bad chain
        raises SnapshotError.  Only ``fsm.transition`` events are re-applied
        (rejections and availability events carry no FSM state).  ``fence``
        must be supplied at the recovered epoch (e.g. the snapshot's); the
        returned executor journals under ``fence.current``.

        Braid rebuild (duck-typed, no import of akash.braid): when a braid
        instance is supplied, journaled ``braid.commit`` events are fed back
        through ``braid.commit`` using the journaled fields.  With a snapshot
        the FULL journal's commit history is replayed (a snapshot stores only
        the root, not the commitments), so the braid root is recomputable
        from the journal alone (M1 acceptance §6a).
        """
        since_seq = 0
        start_state = "S00"
        if snapshot_path is not None:
            snap = load_snapshot(snapshot_path)  # raises SnapshotError on tamper
            since_seq = snap["seq"]
            start_state = snap["fsm_state"]

        tail = replay_events(journal_path, since_seq)  # chain-verified first

        fsm = TenSquaredFSM(start_state)
        for record in tail:
            if record["type"] == "fsm.transition":
                fsm.transition(record["payload"]["event"])

        if braid is not None:
            history = (
                replay_events(journal_path, 0) if snapshot_path is not None else tail
            )
            for record in history:
                if record["type"] != "braid.commit":
                    continue
                payload = record["payload"]
                braid.commit(
                    int(payload["seq"]),
                    str(payload["strand"]),
                    str(payload["bus_line"]),
                    payload.get("crossings") or None,
                )

        journal = EventJournal(journal_path)  # resume the chain for new appends
        executor = cls(fsm, journal, fence, verifier, backend, braid=braid)
        log.info(
            "executor.recovered journal=%s since_seq=%d state=%s epoch=%d",
            journal_path,
            since_seq,
            fsm.state,
            executor.epoch,
        )
        return executor
