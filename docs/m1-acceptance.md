# HELIXos — M1 Acceptance Report: Core Foundation

**Milestone:** M1 (SPEC-M1.md, binding addendum to SPEC.md)
**Deliverable (restated):** a **working Python TEN-SQUARED FSM with verifiable
AKASH braid signatures** — the 100-state FSM executes verified triangulated bus
instructions through `runtime/executor.py`, every applied instruction is woven
into the AKASH braid (`akash/braid.py`), the braid root is signed directly
(`sign_root`) and anchored onto the bus itself (`anchor_braid`), and state is
recoverable from snapshot + journal (`memory/snapshot.py`,
`FSMExecutor.recover`) — all chaos-tested against sidecar failure.
**Status:** **ACCEPTED** — all §6 criteria PASS. Suite: **290 tests green**
(M0 139 + braid 73 + executor/snapshot 72 + M1 acceptance 6);
`scripts/demo_m1.py` exits 0 with **51 checks passed**; `scripts/demo_m0.py`
still exits 0 (no M0 regression).

## §6 acceptance criteria → proof mapping

| # | SPEC-M1 §6 criterion | Proven by | Result |
|---|---|---|---|
| (a) | Braid root recomputable from journal alone | `tests/test_m1_chaos.py::test_recover_equivalence` (journal-only rebuild, root equal, 0 hash mismatches) and `::test_braid_root_tamper_evidence` (`Braid.from_events` re-validates every hash; any tamper → `BraidError`); demo step 4 & 8 & 10 (`from_events` recompute == live root) | **PASS** |
| (b) | Anchor verifies against recomputed root | `tests/test_m1_chaos.py::test_anchor_after_bump` (`verify_anchor` True vs live root, False vs wrong root); demo step 5 (same, against the `from_events`-recomputed root, real sidecar) | **PASS** |
| (c) | `recover()` reproduces exact FSM state + braid root from snapshot+journal | `tests/test_m1_chaos.py::test_recover_equivalence` (randomized 200-instruction run with mid-run collapse → identical FSM state, braid root, epoch) and `::test_tampered_journal_blocks_recover` (1-byte flip → `SnapshotError`, fail-closed); demo step 8 (fresh executor from `FSMExecutor.recover(journal, snapshot)` == original state/root) | **PASS** |
| (d) | Sidecar kill mid-stream → no state corruption, resume works | `tests/test_m1_chaos.py::test_sidecar_kill_resume` (real subprocess: 50 applies → SIGKILL → 10 applies all False, `backend.unavailable` journaled, FSM unchanged, no exception → restart on same socket → 50 more applies True, chain valid); demo step 7 (same flow, 3 strands) | **PASS** |
| (e) | 10k-instruction soak, zero chain/root mismatches | `tests/test_m1_chaos.py::test_soak` — **10,000** instructions across 3 strands through `SimBackend` (~17 s on the dev box; `HELIXOS_SOAK_N` overridable): final `verify_chain()` True, journal-alone root recompute == `Braid.root`, **0** commitment-hash mismatches, reference-FSM state equal | **PASS** |

Supporting (SPEC-M1 §5 phase policy, exercised by both):
`tests/test_m1_chaos.py::test_anchor_after_bump` and demo step 5 — after the
anchor cadence bumps all live pointer generations, a pre-bump instruction is
rejected with reason `phase`; a fresh instruction still applies.

## Braid-signature scheme summary

Each agent writes one **strand** — a hash chain of **commitments**
`sha256(canonical_json({seq, strand, bus_line, prev, crossings}))`. A
commitment may **cross** other strands by naming their tips *at commit time*
(enforced: stale/forged crossing tips are rejected), weaving a causal order
across strands. The **braid root** — `sha256` over the sorted
`{strand: tip}` map — is the epoch's integrity anchor. It is (1) signed
directly as `sign("HELIX-BRAID/1" || root)` (HMAC-dev / Ed25519-prod,
ADR-004), and (2) anchored on the bus as an ordinary `ARCHIVE` instruction
whose stored envelope carries the root, so the anchor itself passes the full
triangulated lock (tag, freshness, replay, cauldron phase). The braid is a
pure function of the journal: `Braid.from_events` rebuilds it from
`braid.commit` events, re-computing and re-validating **every** hash — any
tamper raises `BraidError`.

```
 krishna    natasha    charlotte           anchoring & signing
 ───────    ────────   ─────────           ───────────────────
   k1         n1          │                root = sha256({strand: tip})
   │          │           │                 sorted by strand)
   k2         n2 ──cross──▶│                    │
   │◀──cross── n3          c1 ──cross──▶ k3,n3  ▼
   k3         │           │           sig = sign("HELIX-BRAID/1"‖root)
   │          │           c2              (Ed25519 prod / HMAC dev)
   ▼          ▼           ▼                    │
 tips:    tip(k) tip(n) tip(c)                 ▼
                          │            anchor = [ ptr | ARCHIVE | #tag ]
                          └──────────▶  envelope carries {braid_root,
                                        seq_lo, seq_hi, strands, ts}
                                        verify_anchor: lock ✓ AND
                                        envelope.braid_root == root ✓
```

## Cauldron phase-progression policy (SPEC-M1 §5)

- Slot generations bump **only** via `bump_generation` (sim ABI or sidecar
  RPC), invoked by the **anchor cadence process**: after every braid anchor,
  and on **every** Projective Collapse, for **all live pointers**. The
  executor never bumps — it holds no pointer inventory; its role is
  enforcement.
- Consequence (intended forward security): post-bump, instructions minted
  under the old phase fail `verify_cauldron_phase` and are journaled
  `instruction.rejected` with reason `phase`; freshly minted instructions
  (new slots start at generation 0) still apply. Proven in
  `test_anchor_after_bump` and demo steps 5/9.

## Known limits

- **Single-sequencer signing in the demo**: one HMAC/Ed25519 identity signs
  all three strands' instructions and the root; per-agent keys and a
  distributed sequencer are later-milestone work.
- **LangGraph deferred to M3**: the FSM executor is the plain-Python
  TEN-SQUARED core; LangGraph orchestration integration is M3 scope.
- **Lease timer M3**: possession/lease expiry timing (owner override path)
  remains an M3 item; M1 fencing is epoch-based only.
- The restarted sidecar's store is empty (in-process knotcore state does not
  survive SIGKILL); post-restart traffic must mint fresh instructions —
  pre-crash pointers verify as unknown (rejected `invalid`, fail-closed).
- `FSMExecutor.recover` re-commits journaled `braid.commit` fields without
  requiring `prev` (the executor's own braid.commit payload omits it);
  strict per-hash re-validation is available via `Braid.from_events` on
  fully-journaled commitments (as the demo writes them).
