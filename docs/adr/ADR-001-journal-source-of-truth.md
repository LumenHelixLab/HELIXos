# ADR-001: Append-Only Event-Sourced Journal as the System of Record

| | |
|---|---|
| **Status** | Accepted |
| **Date** | M0 hardening pass |
| **Audit finding(s) resolved** | **AUD-C8** (undefined failure semantics + no source of truth among four state stores); findings-register items **F2.1, F6.1, F6.2** |
| **Governs** | `journal.py` (SPEC §3.7), `epochs.py` (SPEC §3.8), `Council_Ledgers.md`, `Obsidian-Brain/`, zero-copy ephemeral memory |

---

## 1. Context

The v2.0 handoff defined **four overlapping state stores** with no declared system of
record and no consistency model:

1. **KNOTstore** (proprietary binary store) — payload bodies behind TinyPointers.
2. **"Ephemeral" zero-copy memory** — crash-loses-state *by design*, with no rebuild path.
3. **`Council_Ledgers.md`** — a markdown file that two long-running LangGraph "Gods"
   (KALI, KRISHNA) both "manage", with no locking discipline: concurrent writers to one
   file guarantee interleaved-write corruption (multi-writer finding).
4. **Obsidian vault** (`Obsidian-Brain/`, incl. `Akosh_Registry.md`) — human-facing
   cognitive UI, written by no specified process (AUD-H9).

Simultaneously, the flagship safety mechanism — **"Projective Collapse"** — was a name,
not a design: no scope (node or system?), no trigger taxonomy (do transient and
permanent failures both collapse?), no checkpoint cadence, no recovery procedure, and no
epoch announcement (peers acting on stale state = split-brain). Any verification failure
was to "trigger a collapse", yet nothing specified what a collapse *does*, so the real
behavior would have been ad-hoc restarts with silent state divergence.

The audit's fix direction (AUD-C8): a single append-only, event-sourced journal as the
system of record; ledgers and vault as regenerable materialized views; zero-copy memory
as a disposable cache; collapse defined as an epoch-increment + rebuild + broadcast
protocol with peer fencing.

## 2. Decision

### 2.1 Source of truth

**`journal.py` (SPEC §3.7) — an append-only, event-sourced journal — is THE system of
record for HELIXos.** Every state-changing fact (possession transitions with fencing
tokens, bus instructions accepted, epoch increments, snapshot markers, quarantine
events) is appended to the journal before or as it takes effect.

Journal mechanics (binding, per SPEC §3.7):

| Property | Mechanism |
|---|---|
| Format | Newline-delimited JSON, one event per line |
| Line schema | `{"seq":int,"ts":float,"epoch":int,"type":str,"payload":dict,"prev":hex64,"hash":hex64}` |
| Integrity | `hash = sha256(canonical JSON of all fields except "hash")`; `prev` links the chain — tamper-evident, verifiable via `verify_chain()` |
| Durability | Opened `O_APPEND|O_CREAT`; `fsync` on every append |
| Concurrency | Single-writer: `fcntl.flock` exclusive on append (advisory locking, documented); exactly one process owns the journal file |
| API | `append(event_type, payload, epoch=0) -> seq`; `read_all() -> list[dict]`; `verify_chain() -> bool` |

The KNOTstore braid signatures (ADR-004 tags) serve as **integrity anchors** inside
journal payloads — the journal remains the record of *what happened*; KNOTstore holds
*payload bodies* addressed by pointer.

### 2.2 Everything else is derived or disposable

| Store | New status | Rule |
|---|---|---|
| `Council_Ledgers.md` | **Regenerable materialized view** | Rendered from the journal by a single renderer; never hand-edited, never concurrently written. Any corruption is repaired by re-rendering, not by merge |
| Obsidian vault (`Active_Projects/`, `Akosh_Registry.md`) | **Regenerable materialized view** | Sole writer is the `vault-bridge` bot (AUD-H9), which subscribes to journal events and renders markdown; humans write only to inbox files, which are journaled as input events |
| Zero-copy memory | **Disposable cache** | May lose state at any moment without correctness impact; rebuilt from journal replay after any restart. Retains its in-process latency role (docs/latency-budgets.md §5) and loses all durability responsibility |
| KNOTstore | Content-addressed payload store | Holds instruction envelopes; not a log, not ordered, not the record |

Consequence for the multi-writer finding: no file in the system has more than one
writer. The journal has one writer process under `flock`; each view has one renderer.

### 2.3 Projective Collapse — recovery protocol (now defined)

"Projective Collapse" is the controlled reset of a node's derived state. It is a
**protocol**, executed in exactly this order:

1. **Increment epoch.** The collapsing node calls `EpochFence.increment()`
   (SPEC §3.8) and appends an `epoch.increment` event to the journal. The epoch is
   monotonic; it never decreases and never resets.
2. **Rebuild.** Discard all volatile state (zero-copy cache, in-memory views).
   Restore from the latest snapshot marker in the journal, then **replay the journal**
   forward from that marker, re-applying events in `seq` order, verifying the hash
   chain as it goes (`verify_chain()`). A failed chain verification during replay is
   itself a `Tampered` event (§2.4).
3. **Broadcast epoch.** The node announces its new epoch on the bus. Every outbound
   message henceforth carries the new epoch.
4. **Peers fence.** Every peer runs `EpochFence.fences(epoch)`: any message whose
   epoch is **less than** the receiver's current epoch is stale and is fenced —
   dropped and logged, never processed. This is the split-brain defense: pre-collapse
   state cannot leak into the post-collapse world.

Scope: collapse is **per-node** by default. A node-local `Tampered` verdict collapses
that node. System-wide collapse requires an owner-signed command and is journaled as
such; there is no implicit broadcast collapse.

Checkpoint cadence: a snapshot marker event is journaled at least every 10,000 events
or 24 h, whichever first, bounding replay time.

### 2.4 Per-error-class policy (binding)

Verification and I/O failures are classified, and the class — not the mood of the
caller — determines the response:

| Error class | Examples | Policy | Collapse? |
|---|---|---|---|
| `Unavailable` | Sidecar circuit breaker open (ADR-002), IRCd reconnect in progress, transient timeout | **Circuit-break + retry** with backoff; the operation is retried or queued, the node keeps running | No |
| `Tampered` | Tag/signature mismatch (ADR-004), journal hash-chain break, generation-counter mismatch on a live pointer | **Collapse + alert**: execute §2.3, page the owner, quarantine the offending instruction bytes as evidence | Yes (node-local) |
| `SchemaError` | Malformed bus line (fails `BUS_RE`), envelope JSON fails schema, unknown verb | **Quarantine**: record the raw bytes in a quarantine log, drop the message, continue. Never collapses the node — a malformed line is a client bug or noise, not evidence the node is corrupt | No |

This table replaces the v2.0 behavior in which *any* verification failure — including a
transient timeout — was prescribed to trigger an undefined total reset.

## 3. Consequences

### 3.1 Positive

- **One answer to "what is true?"** Disputes are settled by journal replay, not by
  comparing four divergent stores.
- **Multi-writer corruption eliminated by construction**: one writer per file, enforced
  by `flock` on the journal and by single-renderer ownership of each view.
- **Crash recovery becomes a procedure, not a hope**: snapshot + replay is deterministic
  and is exercised by the M3 chaos drill (kill IRCd, kill sidecar, corrupt a ledger —
  system recovers from journal with no split-brain, epoch fencing verified).
- **Projective Collapse is now implementable and testable**: four ordered steps, each
  mapped to `journal.py`/`epochs.py` APIs, each independently assertable in tests.
- **Split-brain is fenced**: stale-epoch messages are dropped by rule, not by luck.
- **Tamper evidence end-to-end**: hash-chained journal + signed bus instructions
  (ADR-004) means both the transport and the record detect modification.
- **Views are free to be pretty**: ledgers and the Obsidian vault can be re-rendered in
  any format, any number of times, because they hold no irreplaceable truth.

### 3.2 Negative

- **fsync per append is a throughput ceiling** (~10³ events/s on consumer storage).
  Acceptable: bus rates at M1 scale are orders of magnitude below this
  (docs/latency-budgets.md; ADR-003 §6). If the ceiling is ever approached, batch
  appends with grouped fsync behind a documented durability window.
- **Journal growth is unbounded** without retention: requires snapshot + rotation
  policy (snapshot markers per §2.3; old segments archived, hashes preserved so the
  chain remains verifiable across rotation).
- **Single-writer is a single point of serialization**: all journaled facts funnel
  through one process. Accepted for M1 scale; the data-plane escape hatch is ADR-003 §6.
- **Replay correctness depends on deterministic event application**: event handlers
  must be pure functions of (state, event); wall-clock reads during replay are
  forbidden. This is a standing code-review rule for `journal.py` consumers.
- **Operational discipline required**: the journal file is now the crown jewel —
  backup/restore and rollback runbooks (AUD-H8 substrate) must treat it as such.

## 4. Alternatives considered

| Alternative | Why rejected |
|---|---|
| **KNOTstore as system of record** | Proprietary blackbox, unordered key/payload store with no log semantics; AUD-H6 already requires it be decoupled behind a sidecar. Anchoring *truth* in the least inspectable component inverts the auditability goal |
| **`Council_Ledgers.md` as record** | Human-editable markdown cannot be hash-chained, fsync-disciplined, or single-writer enforced; it was already the corruption case |
| **Obsidian vault as record** | A UI, not a database; concurrent human+agent edits have no conflict model (AUD-H9) |
| **Distributed consensus (Raft/Paxos) over four agents** | Massive overkill for one host and four agents; adds a cluster subsystem to the critical path of a research prototype. Epoch fencing (§2.3) delivers the needed split-brain defense at 1% of the complexity |
| **CRDT replicated state** | Unordered-merge semantics fight the requirement for a total, auditable order of governance events (possession, epochs); audit needs a log, not a lattice |
| **Off-the-shelf event store (Kafka/NATS JetStream)** | Violates the stdlib-only M0 constraint (SPEC preamble); re-evaluated only if the ADR-003 §6 data-plane trigger fires |

## 5. Audit finding(s) resolved

- **AUD-C8** — resolved in full: declared system of record (§2.1), view/cache
  reclassification (§2.2), defined Projective Collapse with epoch fencing (§2.3),
  per-error-class policy (§2.4).
- **F2.1, F6.1, F6.2** (findings register) — the multi-writer ledger corruption,
  missing rebuild path for ephemeral state, and undefined failure-semantics items are
  closed by §§2.1–2.4 respectively.
