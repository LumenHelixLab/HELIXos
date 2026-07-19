# HELIXos Glossary — M0 Canonical Definitions

**Status:** binding. Companion to `../SPEC.md`. This document defines every load-bearing term flagged in `../../HELIXos_Handoff_Audit.md` §8 (all twenty entries) plus the terms introduced by the M0 hardening design. Where the v2.0 handoff used a term loosely or contradictorily, this glossary assigns the single canonical meaning; conflicting prior usages are superseded. Defined terms are capitalized throughout project documents.

**Entry format**

- **Definition** — what the term means as of M0.
- **Type** — mechanism / data-format / role / concept.
- **Implemented in** — repo path(s) per `SPEC.md` §1, or the milestone that will introduce the artifact.
- **Invariants** — properties an engineer MUST preserve. Relaxing an invariant is a spec change, not an implementation detail.

## Index

| # | Term | Type | Section |
|---|------|------|---------|
| 1 | TEN-SQUARED | mechanism (FSM) | §1.1 |
| 2 | Numo Zero-Copy | concept / hot-path discipline | §1.2 |
| 3 | Projective Collapse | mechanism (recovery protocol) | §1.3 |
| 4 | epoch | data (system generation counter) | §1.4 |
| 5 | fencing token | mechanism (authorization freshness) | §1.5 |
| 6 | EventJournal | mechanism + data-format (system of record) | §1.6 |
| 7 | Layer A | concept (architecture stratum) | §1.7 |
| 8 | "O(1)" claim | concept (scoped performance claim) | §1.8 |
| 9 | Triangulated Bus | data-format + mechanism | §2.1 |
| 10 | TinyPointer | data-format (slot identifier) | §2.2 |
| 11 | Cauldron canonical semantics / `verify_cauldron_phase` | mechanism (anti-replay anchor) | §2.3 |
| 12 | braid signature vs. HMAC tag | data-format (authenticator) | §2.4 |
| 13 | AKASH / Akosh | role (store) + data-format (view) | §2.5 |
| 14 | sidecar | mechanism (process isolation / IPC) | §2.6 |
| 15 | BABEL / Tower of Babel Dispatcher | mechanism (command router) | §3.1 |
| 16 | manifest command grammar | data-format (command grammar) | §3.2 |
| 17 | !POSSESS grammar | data-format + mechanism (governance) | §3.3 |
| 18 | RGB workflow | concept / mechanism (governance pipeline) | §3.4 |
| 19 | +mode privilege governance | concept (non-authoritative UX layer) | §3.5 |
| 20 | DMZ | concept (trust model) | §3.6 |
| 21 | Quaternity | role (agent set) | §4.1 |
| 22 | Spiders / Gods | role (agent classes) | §4.2 |
| 23 | vault-bridge | role / mechanism (sole vault writer) | §4.3 |
| 24 | Council_Ledgers.md | data-format (rendered view) | §4.4 |
| 25 | LangGraph | concept (deferred external dependency) | §4.5 |
| 26 | Thought/Thinking channel partitioning | concept + mechanism (channel taxonomy) | §5.1 |
| 27 | #t-gateway | role (ingress channel) | §5.2 |
| 28 | Shorthand / AFSK | mechanism (planned translators) | §5.3 |

---

## 1. Runtime, state, and recovery

### 1.1 TEN-SQUARED

- **Definition:** The deterministic 100-state finite-state machine at the core of the aigent-os-kernel runtime: a 10×10 grid of states named `S00`–`S99` (state `S(r,c)`, row `r`, column `c`, each in `0..9`), driven by ten events `E0`–`E9`. The name denotes the grid: ten rows squared with ten columns. The reference transition rule, materialized once at init as a tuple-of-tuples, is `S(r,c) --Ee--> S(r',c')` where `r' = (r + e) mod 10` and `c' = (c*3 + e + r) mod 10`. The performance contract attaches to in-process transitions only: p99 < 1000 µs per transition (per `docs/latency-budgets.md`), measured with `time.perf_counter_ns`, a pre-created event list, and `gc.disable()` during measurement; `benchmark()` reports `p50_us`/`p99_us`/`p999_us`. The bound never applies to bus delivery (audit AUD-C7 splits the budget per layer).
- **Type:** mechanism (deterministic FSM).
- **Implemented in:** `aigent-os-kernel/src/runtime/ten_squared_fsm.py` (SPEC §3.9).
- **Invariants:**
  1. Exactly 100 states (`S00`–`S99`) and exactly 10 events (`E0`–`E9`); adding or removing a state or event is a spec change.
  2. The transition table is materialized at init as a tuple-of-tuples; no dict lookups and no allocation on the hot path.
  3. Transitions are total and deterministic: every (state, event) pair has exactly one successor, identical on every node.
  4. The latency claim is in-process p99 only and must be backed by the shipped `benchmark()` harness.

### 1.2 Numo Zero-Copy

- **Definition:** The kernel's in-process, allocation-free state-access discipline. In M0 it is **not** a library and **not** cross-process shared memory; it names three commitments: (a) the FSM hot path performs zero per-transition allocation (pre-materialized tables, no dict lookups); (b) ephemeral runtime state is a disposable cache — never a system of record — whose crash loss is by design, with recovery by EventJournal replay (audit AUD-C8); (c) no serialization round trips occur *inside* a process — serialization happens exactly once, at the bus boundary, where it is mandatory (audit AUD-C7: the v2.0 "avoid standard serialization" mandate is impossible across a socket and is reinterpreted accordingly). Cross-process state moves by message passing only (audit AUD-M5 share-nothing model). If shared memory is ever introduced after M0, it must use versioned slots with seqlocks.
- **Type:** concept (engineering discipline) governing the hot path.
- **Implemented in:** hot path in `aigent-os-kernel/src/runtime/ten_squared_fsm.py` (SPEC §3.9); journaled state in `aigent-os-kernel/src/memory/` (SPEC §3.7–3.8).
- **Invariants:**
  1. No allocation on the FSM transition hot path.
  2. Every ephemeral cache is rebuildable from the EventJournal; nothing ephemeral is authoritative.
  3. No cross-process mmap or shared-memory buffer in M0.
  4. "Zero-copy" is never used to justify bypassing the wire format (§2.1) at a process boundary.

### 1.3 Projective Collapse

- **Definition:** The system's fail-closed state-reset protocol, triggered when a verification failure is attributable to tampering. One collapse is exactly: increment the monotonic epoch via `EpochFence.increment()` → discard all volatile state → rebuild from the latest snapshot plus EventJournal replay → announce the new epoch → fence every message stamped with an older epoch (`EpochFence.fences(epoch)` returns `True` for `epoch < current`). Trigger taxonomy (audit AUD-C8 fix, normative): **Tampered** → collapse + alert; **Unavailable** (sidecar or bus down) → circuit-break/retry, never a collapse; **SchemaError** → quarantine the offending record, no collapse. Scope in M0 is node-local (single node); epoch announcement across peers rides the M2 bus. Trigger authority: any verifying agent (a local, fail-closed safety decision) and the Human Owner (deliberate reset). Checkpoint cadence: the journal is the durable record (fsync per append); snapshot cadence is an operator parameter, not M0 code.
- **Type:** mechanism (recovery protocol).
- **Implemented in:** `aigent-os-kernel/src/memory/epochs.py` (`EpochFence`, SPEC §3.8); replay source `aigent-os-kernel/src/memory/journal.py` (SPEC §3.7); triggers are `Tampered`-class failures of `verify_instruction_lock` in `aigent-os-kernel/KNOTstore_bin/KNOT_API_WRAPPER.py` (SPEC §3.3).
- **Invariants:**
  1. Epochs are strictly monotonic and never reused.
  2. Transient unavailability NEVER triggers a collapse.
  3. Every collapse is journaled with old epoch, new epoch, and cause.
  4. After a collapse, all stale-epoch messages are fenced — no split-brain continuation.

### 1.4 epoch

- **Definition:** The system generation counter (non-negative int, starts 0) maintained by `EpochFence`; the unit of systemic freshness. Incremented exactly once per Projective Collapse. Every journal line carries an `epoch` field; any message, instruction, or ledger entry stamped with `epoch < current` is stale and must be fenced. Distinct from the fencing token (§1.5, possession-scoped) and from the cauldron phase (§2.3, slot-scoped): three monotonic counters at three different scopes.
- **Type:** data (scalar) used as a fencing basis.
- **Implemented in:** `aigent-os-kernel/src/memory/epochs.py` (SPEC §3.8); stamped by `aigent-os-kernel/src/memory/journal.py` (SPEC §3.7).
- **Invariants:**
  1. Monotonic, never decremented, never reset except by process bootstrap from the journal's last record.
  2. Incremented only through `EpochFence.increment()` (the Projective Collapse trigger).
  3. Staleness is judged only via `fences()` (`epoch < current`).

### 1.5 fencing token

- **Definition:** A monotonically increasing integer owned by `KrishnaManifestor`, incremented on **every possess transition** and embedded in every possession audit record. It totally orders possession leases: a `manifest()` call is valid only under the current fencing token, so a stale or replayed grant is detectable and refused. Distinct from the epoch (§1.4): the fencing token is scoped to one manifestor instance's possession lifecycle, not to the system.
- **Type:** mechanism (authorization freshness).
- **Implemented in:** `aigent-os-kernel/orchestrator/possession.py` (SPEC §3.6: `self.fencing_token: int`, included in the audit record).
- **Invariants:**
  1. Increments on every possess transition; never reused within the process lifetime.
  2. Read and mutated only under the manifestor's `threading.Lock`.
  3. Present in every possession audit record.

### 1.6 EventJournal

- **Definition:** The append-only, event-sourced **system of record** for HELIXos (audit AUD-C8 fix). A JSON Lines file opened `O_APPEND|O_CREAT`, fsync'd after every append. Line schema: `{"seq":int,"ts":float,"epoch":int,"type":str,"payload":dict,"prev":hex64,"hash":hex64}`, where `hash = sha256(canonical JSON of all fields except hash)` and `prev` chains the previous line's hash; `verify_chain()` re-verifies the whole chain. Single writer per file, enforced by an exclusive `fcntl.flock` on append (advisory, documented). Everything else — `Council_Ledgers.md`, `Akosh_Registry.md`, zero-copy caches — is a regenerable materialized view or a disposable cache over this journal.
- **Type:** mechanism + data-format.
- **Implemented in:** `aigent-os-kernel/src/memory/journal.py` (SPEC §3.7); path from env `HELIXOS_JOURNAL_PATH` (default `./helixos-journal.jsonl`).
- **Invariants:**
  1. Append-only: no update, no delete, no compaction in place (rotation creates a new file chained from the old one's last hash).
  2. fsync completes before the append is acknowledged (`append()` returns `seq`).
  3. `verify_chain()` is true at all times; a broken chain is a Tampered-class event (§1.3).
  4. Exactly one writer process per file (flock enforced).

### 1.7 Layer A

- **Definition:** The bottom, deterministic stratum of the four-layer HELIXos model defined by M0 (v2.0 named "Layer A" with no layer model; audit §8):
  - **Layer A — aigent-os-kernel:** TEN-SQUARED FSM, EventJournal/epochs, BABEL dispatcher, KNOTstore bridge and sidecar. All M0 code lives here.
  - **Layer B — transport / nervous system:** HelixIRCd and the channel fabric (M2, §5).
  - **Layer C — agent layer:** the Quaternity and the HELIXvault toolbelt (M3, §4).
  - **Layer D — human cognitive UI:** the Obsidian-Brain vault rendered by vault-bridge (M3, §4.3).

  v2.0's Milestone-1 phrase "Layer A & Akosh Integration" means: bring up this bottom layer and its binary-stable store bridge. Dependencies point only downward; Layer A must boot and pass its full test suite with Layers B–D absent.
- **Type:** concept (architecture stratum).
- **Implemented in:** the entire `aigent-os-kernel/` tree (SPEC §1).
- **Invariants:**
  1. Layer A has no upward dependency: no import of IRC, agent, or vault code.
  2. Every Layer-A component is testable headless (no IRC daemon, no vault required).

### 1.8 "O(1)" claim

- **Definition:** v2.0 claimed O(1) TinyPointer resolution. M0 scopes the claim: `store_instruction`, `fetch_payload`, and `verify_cauldron_phase` in the reference simulator are expected-O(1) slot-table lookups over a bounded capacity (default `2**16` slots). The claim covers **indexing cost only**. It does not cover end-to-end bus latency, which is governed by the per-layer budgets (in-process FSM p99 < 1 ms; localhost bus delivery p99 < 10 ms in M2). The claim is retained only in this scoped form; any broader use requires benchmark evidence with stated percentiles, load, and hardware.
- **Type:** concept (scoped performance claim).
- **Implemented in:** `aigent-os-kernel/KNOTstore_bin/knotcore_sim.py` (SPEC §3.2).
- **Invariants:**
  1. Capacity is bounded; exhaustion raises `StoreFull` — never a wraparound, never an alias (audit AUD-H2).
  2. No latency statement without measured p50/p99/p999.

---

## 2. The Triangulated Bus and the KNOTstore

### 2.1 Triangulated Bus

- **Definition:** Both (a) a wire format and (b) the verification pipeline around it. **Wire format:** the line `[ {ptr} | {verb} | #{tag} ]`, matched by `BUS_RE` (SPEC §2): `ptr` is a TinyPointer (§2.2, 16 lowercase hex chars), `verb` matches `[A-Z0-9_]{1,16}` and must be in `ALLOWED_VERBS = {READ, WRITE, EXEC, ARCHIVE}`, `tag` is the braid signature (§2.4) — 22 base64url chars in HMAC mode, up to 86 in Ed25519 mode. The three "axes" are: pointer (where the payload lives), verb (what to do), tag (proof that pointer + verb + stored envelope are authentic). **Pipeline:** generation = validate inputs (verb allowlist, key length) → store the freshness envelope (compact JSON `{"ts":int,"nonce":b64url(12B),"body":payload_text}`) → authenticate the canonical encoding → emit the line; verification = strict regex parse → fetch the stored envelope → clock-skew window (`MAX_CLOCK_SKEW_S = 300`) and seen-nonce replay check → recompute-and-compare the tag in constant time → cauldron phase check (§2.3) → accept; ANY exception fails closed (`False`, logged). The emitted line is published to the bus (M2: IRC; M0: in-process and tests).
- **Type:** data-format + mechanism.
- **Implemented in:** wire grammar and canonical encoding in `SPEC.md` §2; pipeline in `aigent-os-kernel/KNOTstore_bin/KNOT_API_WRAPPER.py` (SPEC §3.3); signers in `aigent-os-kernel/KNOTstore_bin/signers.py` (SPEC §3.1).
- **Invariants:**
  1. The tag always covers the canonical `b"HELIX-BUS/2"` length-prefixed encoding of ptr ‖ verb ‖ stored-envelope — never the payload alone (audit AUD-C4).
  2. Verbs come only from `ALLOWED_VERBS`; generation raises on anything else (audit AUD-M1).
  3. Verification never raises; it returns `False` on any error (audit AUD-C1).
  4. The freshness envelope (`ts`, `nonce`, `body`) is mandatory; replay protection = 300 s skew window + seen-nonce cache (audit AUD-C5).

### 2.2 TinyPointer

- **Definition:** The 64-bit, write-once slot identifier returned by `store_instruction`, rendered on the wire as exactly 16 lowercase hex characters (`[0-9a-f]{16}`). Write-once means the slot's payload is fixed at store time and never mutated; M0 performs no reclamation and no reuse — exhaustion raises `StoreFull`. Freshness after mutation-like events is expressed through the slot's cauldron phase (§2.3), never through pointer reuse.
- **Type:** data-format (identifier).
- **Implemented in:** `aigent-os-kernel/KNOTstore_bin/knotcore_sim.py` `store_instruction` (SPEC §3.2); wire grammar in SPEC §2.
- **Invariants:**
  1. Exactly 16 lowercase hex chars on the wire; generation validates the returned pointer's format and raises on anything else (audit AUD-H4).
  2. Never reused, never recycled, never aliases another payload (audit AUD-H2).
  3. 64-bit space; the sim's default table is `2**16` slots, configurable via `configure(capacity)`.

### 2.3 Cauldron canonical semantics / `verify_cauldron_phase`

- **Definition:** "The Cauldron" is the KNOTstore slot array viewed as a state machine. The **cauldron phase** of a slot is its monotonically increasing generation counter: it starts at 0 when the slot is stored, and `bump_generation(ptr)` advances it, invalidating older tags' phase checks. **Canonical semantics** is the single agreed reading of a slot: one ptr → one immutable envelope → one current phase. `verify_cauldron_phase(ptr, tag, phase) -> bool` is the store-side predicate, true iff the ptr exists AND `phase` equals the slot's current generation-phase. It is the final gate of instruction verification and is always called with a caller-supplied expected phase — never a hardcoded constant (audit AUD-H3). Signature per SPEC §3.2: `(ptr: str, tag: str, phase: int) -> bool`.
- **Type:** mechanism (anti-replay / freshness anchor).
- **Implemented in:** `aigent-os-kernel/KNOTstore_bin/knotcore_sim.py` (SPEC §3.2); invoked from `verify_instruction_lock` in `KNOT_API_WRAPPER.py` (SPEC §3.3); exposed over the sidecar RPC (SPEC §3.4).
- **Invariants:**
  1. Per-slot phase is monotonic and starts at 0.
  2. The expected phase is supplied by the caller; it is never frozen in code.
  3. The predicate is a pure check: it mutates nothing.
  4. Authority to bump a generation follows the privilege matrix (`docs/security-model.md` §4).

### 2.4 braid signature vs. HMAC tag

- **Definition:** One object, two names — resolved. The **braid signature** is the third axis of the Triangulated Bus: the authenticator that braids ptr + verb + stored envelope into a single value (this is the M1 deliverable named "verifiable AKASH braid signatures"; v2.0's "treat addresses as immutable braid signatures" advisory refers to the same object). **Tag** is its wire rendering in the `#...` field. Two modes (SPEC §3.1): **dev HMAC mode** — 16-byte truncated HMAC-SHA-256 rendered as 22 base64url chars, verified with `hmac.compare_digest`; **production Ed25519 mode** — the FULL 64-byte signature rendered as 86 base64url chars (a truncated Ed25519 signature is unverifiable because truncation breaks the library verifier, so the full signature rides in the tag field and the `BUS_RE` tag group widens to `[A-Za-z0-9_-]{22,86}`). `signers.tag_length` exposes the active mode's length; the wrapper formats accordingly. Exactly one canonical representation per mode exists at the ABI boundary (audits AUD-H3, AUD-L1).
- **Type:** data-format (authenticator) + mechanism.
- **Implemented in:** `aigent-os-kernel/KNOTstore_bin/signers.py` (SPEC §3.1); canonical input in SPEC §2; mode decision recorded in `docs/adr/` ADR-004.
- **Invariants:**
  1. Always computed over the `HELIX-BUS/2` canonical encoding of all three axes.
  2. HMAC keys are ≥ 32 bytes (`ValueError` otherwise); Ed25519 signatures are never truncated.
  3. HMAC comparison is constant-time (`hmac.compare_digest`).
  4. Tag length comes from `signers.tag_length`, never a hardcoded constant.

### 2.5 AKASH / Akosh

- **Definition:** Canonical naming resolved (one name per artifact). **AKASH** is the KNOTstore itself — the binary-stable instruction store behind the knotcore ABI: ptr → immutable envelope, with cauldron phases. "Verifiable AKASH braid signatures" (the M1 deliverable) therefore means: bus lines whose tags verify against AKASH-resident envelopes per §2.1. **`Akosh_Registry.md`** is the human-readable, append-only markdown view of stored instructions in the Obsidian vault, written ONLY by vault-bridge (§4.3) from journal events; it is a regenerable materialized view, never a store of record, and never hand-edited.
- **Type:** role (store) + data-format (view).
- **Implemented in:** store: `aigent-os-kernel/KNOTstore_bin/knotcore_sim.py` and `sidecar/` (SPEC §3.2, §3.4); view: `Obsidian-Brain/Akosh_Registry.md` via vault-bridge (M3; M0 skeleton README per SPEC §1).
- **Invariants:**
  1. AKASH is written only through the wrapper/sidecar API — no direct slot mutation.
  2. The Registry is written only by vault-bridge and is regenerable from the EventJournal.
  3. The store is authoritative; on any disagreement the Registry is rebuilt, never patched.

### 2.6 sidecar (knotcore sidecar)

- **Definition:** The out-of-process home of the KNOTstore (audit AUD-H6 fix): a Unix-socket RPC service that wraps `knotcore_sim` today and the proprietary `knotcore.so` later via the same ABI, so that an FFI fault can never kill an agent process. `rpc_protocol.py`: newline-delimited JSON frames `{"id":int,"method":str,"params":dict}` → `{"id":int,"result":...}` or `{"id":int,"error":str}`, with a 1 MiB frame cap. `sidecar_server.py`: threading per connection; methods `store_instruction`, `fetch_payload`, `verify_cauldron_phase`, `health` (→ `{"status":"ok","abi_version":1}`), `abi_version`; graceful SIGTERM. `sidecar_client.py`: `KnotClient` with a per-call deadline (`timeout=2.0` s) and a circuit breaker (after `breaker_threshold=5` consecutive errors the breaker opens, calls raise `Unavailable` immediately, and after `breaker_reset_s=30` s it goes half-open).
- **Type:** mechanism (process isolation / IPC).
- **Implemented in:** `aigent-os-kernel/KNOTstore_bin/sidecar/{rpc_protocol.py, sidecar_server.py, sidecar_client.py}` (SPEC §3.4); socket from env `HELIXOS_SIDECAR_SOCKET` (default `/tmp/helixos-knotcore.sock`).
- **Invariants:**
  1. The ABI is versioned (`abi_version = 1` handshake at client connect).
  2. Every call is deadline-bounded; there is no unbounded wait on the store.
  3. The sidecar is the only process touching the store; agents never link `knotcore.so` in-process.
  4. `Unavailable` maps to circuit-break/retry — never to a Projective Collapse (§1.3).

---

## 3. Command and governance

### 3.1 BABEL / Tower of Babel Dispatcher

- **Definition:** The kernel's command router — the only path by which a text command becomes an executed action. `BabelDispatcher.register(verb, handler)` binds verbs to handlers; `dispatch_direct(command)` runs the **BABEL dispatch protocol**: validate → audit → execute. Validation: the command is a `str`, non-empty, ≤ 400 bytes UTF-8, containing no `\r`, `\n`, or `\x00`; the first whitespace-delimited token (uppercased) must be a registered verb, else `UnknownCommand`. Every dispatch is audited as `babel.dispatch cmd=%r` through the injected audit callable. Relation to `babel-lang`: `HELIXvault/babel-lang/` holds translation modules (Shorthand/AFSK, §5.3) that may *produce* command text; the BABEL dispatcher in `src/BABEL/` is the sole consumer and executor — translation never bypasses dispatch (audit AUD-M6 reconciles the two "Babel" names: one router, one translator library).
- **Type:** mechanism (command router).
- **Implemented in:** `aigent-os-kernel/src/BABEL/dispatcher.py` (SPEC §3.5); exceptions `CommandError` and `UnknownCommand(CommandError)`.
- **Invariants:**
  1. No execution path around `dispatch_direct`; it is the single choke point.
  2. Order is fixed: validate, then audit, then execute.
  3. An unregistered verb raises `UnknownCommand` — never a heuristic match.
  4. Every dispatch attempt is audited, including refused ones.

### 3.2 manifest command grammar

- **Definition:** The exact grammar of commands accepted by `KrishnaManifestor.manifest()`: `command = <VERB> [ <arg> ... ]`, where `VERB` is the first whitespace-delimited token, uppercased, and must be registered with the `BabelDispatcher`; the whole command is a `str`, non-empty after `strip()`, ≤ 400 bytes UTF-8, and must not contain `\r`, `\n`, or `\x00`. Error surface: `PossessionDenied` when unpossessed; `PossessionError` on empty, oversize, or control-character input; `UnknownCommand` from the dispatcher for unregistered verbs. There is no shell: no quoting, pipes, redirection, globbing, or expansion — tokens are passed to the handler as `list[str]`.
- **Type:** data-format (command grammar).
- **Implemented in:** `_validate_command` and `manifest` in `aigent-os-kernel/orchestrator/possession.py` (SPEC §3.6); validation shared with `aigent-os-kernel/src/BABEL/dispatcher.py` (SPEC §3.5).
- **Invariants:**
  1. The 400-byte cap and control-character rejection are applied identically at the possession boundary and the dispatcher boundary (defense in depth, audit AUD-C3).
  2. Refusal is an explicit exception, never a silent `None`.
  3. The grammar never grows shell features; new capability arrives as new registered verbs.

### 3.3 !POSSESS grammar

- **Definition:** The owner-override command surface. **M0 binding (in-process):** possession is exercised through `KrishnaManifestor.toggle_possession(owner_token) -> str`, returning `MANIFESTOR_MODE: True|False`; errors are `PossessionDenied` (invalid token or rate-limited). **Wire grammar (normative for M2/M3, when bound to IRC):** `!POSSESS <owner_token>` sent as a *private* message to the orchestrator — never in a public channel — with replies `MANIFESTOR_MODE: True` (granted), `MANIFESTOR_MODE: False` (released), `ERR DENIED` (bad token), `ERR RATE-LIMITED`. Binding to `+mode`: possession is an application-level, token-authenticated grant ordered by a fencing token and bounded by a lease; IRC `+o`/`+mode` state is at most a UX hint and is never authoritative (audits AUD-H5, AUD-M8). The full protocol — authenticate → fence → lease → release — is specified in `docs/security-model.md` §5.
- **Type:** data-format (command) + mechanism (governance).
- **Implemented in:** `aigent-os-kernel/orchestrator/possession.py` (SPEC §3.6); the IRC binding is deferred to M2/M3 per audit §7.
- **Invariants:**
  1. The token is compared as a SHA-256 hash in constant time; the raw token is never stored, never logged, never sent to a public channel.
  2. Every transition is audited and carries the fencing token.
  3. Fail-closed: any error leaves the system unpossessed.
  4. Only the `KRISHNA` agent can be wrapped (construction raises `ValueError` otherwise).

### 3.4 RGB workflow

- **Definition:** The default three-stage governance pipeline for agent-initiated action — the pipeline that possession deliberately bypasses. M0 assigns the canonical meaning (v2.0 left it undefined; audit §8): **R = Review** — the triangulated instruction is parsed and verified (tag, freshness, cauldron phase; §2.1); **G = Gate** — the policy decision: verb allowlist, dispatcher validation, and the privilege-matrix check (`docs/security-model.md` §4); **B = Broadcast** — dispatch via BABEL and append the outcome to the EventJournal. Stages run strictly in order; any failure aborts before side effects. `!POSSESS` + `manifest()` skips R and G because the owner *is* the policy; the compensating controls are owner-token authentication, the fencing token, rate limiting, command validation, and audit of every manifest (audit AUD-C3).
- **Type:** concept (workflow) / mechanism.
- **Implemented in:** R = `verify_instruction_lock` in `KNOT_API_WRAPPER.py` (SPEC §3.3); G = validation in `dispatcher.py` (SPEC §3.5) plus the privilege matrix; B = `dispatcher.py` + `journal.py` (SPEC §3.7).
- **Invariants:**
  1. Every non-possession action traverses all three stages in order.
  2. The possession bypass never skips command validation or audit.
  3. A failed stage leaves no side effects.

### 3.5 +mode privilege governance

- **Definition:** The use of IRC channel modes (`+o` operator, `+v` voice, moderated `+m`, invite-only `+i`) as a presentation-layer privilege hint on the M2 bus. Canonical M0 position (audits AUD-H5, AUD-M8): channel modes are NEVER an authorization source — identity behind a nick is unauthenticated until TLS + SASL exist, and mode changes are unaudited. Real authorization is application-level: verified braid signatures, signed commands, and owner-token possession (`docs/security-model.md` §7). In M2, `+mode` is applied as defense in depth (e.g. `+m` on `#t-gateway` with voice granted only to the four SASL-authenticated Quaternity nicks) purely to reduce noise and accidental misuse.
- **Type:** concept (governance layer, non-authoritative).
- **Implemented in:** no M0 code; M2 HelixIRCd; normative rules in `docs/security-model.md` §7.
- **Invariants:**
  1. No security decision may consult channel mode state.
  2. Every privileged operation carries an application-level credential.
  3. Mode changes on governance channels are logged.

### 3.6 DMZ

- **Definition:** The trust posture of the bus — a stance, not a network segment. Everything ON the bus (Layer B) is treated as a demilitarized zone: transport identities are untrusted, lines are forgeable, relays log plaintext. Inside the boundary: agent processes, the KNOTstore sidecar, and the journal host filesystem. Outside: anything arriving from the bus. Allowed crossings are exactly: (a) triangulated instructions that pass the full verification pipeline; (b) BABEL commands that pass validation; (c) possession requests presenting a valid owner token. Everything else is dropped by construction (fail-closed). This honors v2.0's "DMZ-style IRC nervous system" in posture while applying the audit's correction (AUD-H5: a flat trust domain is not a DMZ) through application-level authentication rather than network segmentation.
- **Type:** concept (trust model).
- **Implemented in:** enforced by `verify_instruction_lock` in `KNOT_API_WRAPPER.py` (fail-closed), validation in `dispatcher.py`, and `possession.py`; channel hardening specified in `docs/security-model.md` §7.
- **Invariants:**
  1. No implicit trust from transport identity (nick, host, mode).
  2. Every crossing is authenticated at the application layer.
  3. Secrets never cross the bus: the owner token and all keys live in env only (SPEC §3.10).

---

## 4. Agents and organization

### 4.1 Quaternity

- **Definition:** The fixed set of four M3 agents: NATASHA and CHARLOTTE (the Spiders), KALI and KRISHNA (the Gods). "Four" is a design constant of M0–M3; scalability beyond it is a post-M3 question with quantified limits (audit AUD-M7). Identities, credentials, and privileges are fixed by `docs/security-model.md` §2 and §4.
- **Type:** role (agent set).
- **Implemented in:** M3 (`aigent-os-kernel/orchestrator/` + `HELIXvault/robot-agents/`, M0 skeleton per SPEC §1); the M0 artifacts they depend on are `possession.py` and `dispatcher.py`.
- **Invariants:**
  1. Exactly these four identities appear in M0–M3 governance artifacts; a fifth principal is a spec change.
  2. Each agent holds its own Ed25519 keypair in production mode (§2.4).
  3. No agent is also a human principal; the Human Owner is a separate actor.

### 4.2 Spiders / Gods

- **Definition:** The two agent classes of the Quaternity. **Spiders (NATASHA, CHARLOTTE):** lightweight, event-driven agents subscribed to `#t-gateway` (§5.2) that handle API-related requests: they generate and verify triangulated instructions and submit BABEL commands, and they hold no governance authority. **Gods (KALI, KRISHNA):** long-running persistent processes with system duties — KALI is the Ledger-keeper (owns ledger-facing duties and watches EventJournal hash-chain integrity), KRISHNA is the Manifestor vessel (the only agent possession may bind to; `KrishnaManifestor` refuses construction for any other `agent_id`). The class name "God" grants no inherent privilege: all authority flows from the Human Owner through the privilege matrix (audit AUD-M3).
- **Type:** role.
- **Implemented in:** KRISHNA-only construction in `aigent-os-kernel/orchestrator/possession.py` (SPEC §3.6); agent homes per SPEC §1 (M3).
- **Invariants:**
  1. Possession binds only to KRISHNA.
  2. Spiders never receive `manifest` or `!POSSESS` capability.
  3. Agent authority derives from the privilege matrix, never from the class name.

### 4.3 vault-bridge

- **Definition:** The named service that is the SOLE writer of the Obsidian-Brain vault (audit AUD-H9 fix). It subscribes to bus/journal events and renders the markdown views — `Council_Ledgers.md`, `Akosh_Registry.md`, `Active_Projects/`. Humans write only to designated inbox files; vault-bridge consumes inbox files as input and never shares write access to the rendered views. Concurrent human+agent writes are impossible by construction: one writer.
- **Type:** role (service) / mechanism.
- **Implemented in:** M3 service; M0 anchors are `journal.py` (its input) and the `Obsidian-Brain/` skeleton (SPEC §1).
- **Invariants:**
  1. Single writer to every rendered vault file.
  2. Rendered views are regenerable from the EventJournal.
  3. Inbox files are the only human-writable path into the vault.

### 4.4 Council_Ledgers.md

- **Definition:** The rendered markdown record of God-agent (KALI/KRISHNA) deliberations and decisions, at path `Obsidian-Brain/Council_Ledgers.md`. Append-only with rotation; every entry carries the epoch and the journal `seq` it was rendered from. Writer: vault-bridge only, from journal events. Readers: humans and agents. v2.0's "two Gods both manage one markdown file" is superseded (audit AUD-C8: two writers on one file guarantee interleaved-write corruption) — the EventJournal is the record; the ledger is a view. Relation to `Akosh_Registry.md`: the Registry logs KNOTstore instructions (bus traffic); the Ledger logs agent decisions — two views over one journal.
- **Type:** data-format (rendered view) / role.
- **Implemented in:** `Obsidian-Brain/Council_Ledgers.md` (M3, via vault-bridge); system of record `aigent-os-kernel/src/memory/journal.py` (SPEC §3.7).
- **Invariants:**
  1. Single writer (vault-bridge); append-only with rotation.
  2. Every entry is stamped with epoch and journal seq.
  3. Never a system of record; rebuilt from the journal on any inconsistency.

### 4.5 LangGraph

- **Definition:** The third-party agent-orchestration framework v2.0 named as the runtime of the Gods ("long-running persistent processes (LangGraph)"). M0 resolution of this undeclared dependency (audit §9: BOM missing): LangGraph is **not** an M0 dependency — M0 is stdlib-only plus `cryptography` 44.x and `pytest` 9.x (SPEC header). The adoption decision is deferred to M3; if adopted it must be pinned (version and hash), must run inside God processes only, and must never enter Layer A.
- **Type:** concept (external dependency, deferred).
- **Implemented in:** nowhere in M0 (deliberately).
- **Invariants:**
  1. No LangGraph import anywhere in Layer A.
  2. Any M3 adoption updates the BOM and the threat model (`docs/security-model.md` §6) in the same change.

---

## 5. Transport and channels (M2-forward)

### 5.1 Thought/Thinking channel partitioning

- **Definition:** The channel taxonomy of the Layer-B bus. Every channel is exactly one of two classes. **Thought channels** (name prefix `#t-`): machine-actionable — they carry Triangulated Bus lines and BABEL commands, and a line is actionable only after the full verification pipeline (§2.1) passes. **Thinking channels** (no `#t-` prefix; convention `#think-`): deliberative — free-form agent and human prose that is never dispatched and never quoted into a command. Enforcement: BABEL and the wrapper accept input only from Thought channels, and the wire grammar itself (`BUS_RE`, verb allowlist, 400-byte cap) rejects prose; Thinking-channel content may influence an agent's model output but cannot cross into an action without being re-originated as a verified instruction (DMZ posture, §3.6). The ACL sketch lives in `docs/security-model.md` §7.
- **Type:** concept (channel taxonomy) + mechanism (ACL).
- **Implemented in:** M2 HelixIRCd channel registry; M0 anchors are `BUS_RE` and `ALLOWED_VERBS` (SPEC §2) and dispatcher validation (SPEC §3.5).
- **Invariants:**
  1. Actionability attaches to the channel class, never to message content alone.
  2. Thinking channels are non-authoritative by construction.
  3. The partition and every channel's class are recorded in the M2 channel registry.

### 5.2 #t-gateway

- **Definition:** The well-known Thought channel on which external and API-related requests enter the bus; the Spiders' (NATASHA/CHARLOTTE) primary subscription. Its purpose is to be a single, monitored ingress point for request traffic so that ACLs, rate limits, and audit attach to one channel. ACL (M2): only SASL-authenticated Quaternity nicks hold voice; the owner console reads; every posted line must still pass verification to be actionable. Relation to the partition: `#t-gateway` is a Thought channel (`#t-` prefix) and the first entry of the M2 channel registry.
- **Type:** role (channel).
- **Implemented in:** M2 HelixIRCd channel registry; M0 anchor is the `#t-` taxonomy (§5.1).
- **Invariants:**
  1. Ingress-only for requests: no deliberation on `#t-gateway`.
  2. Every line is subject to the full verification pipeline before any action.
  3. Per-sender token-bucket rate limiting applies from M2 (audit AUD-M7).

### 5.3 Shorthand / AFSK (babel-lang modules)

- **Definition:** The translation-module family of `HELIXvault/babel-lang/`. **Shorthand:** the human macro-notation expansion module — expands owner shorthand into canonical BABEL command text (planned). **AFSK:** the audio frequency-shift keying modem module — encodes bus lines for out-of-band or audio transports (planned). M0 status: named and bounded so the terms stop floating (audit §8); there is no M0 code. Interface contract when built: input text/bytes → output that MUST conform to the manifest command grammar (§3.2) or the bus wire format (§2.1). Translation modules produce *candidates* only — the dispatch and verification gates are unchanged, and a translator can never mint authority.
- **Type:** mechanism (planned translators) / data-format.
- **Implemented in:** `HELIXvault/babel-lang/` (M0 skeleton per SPEC §1).
- **Invariants:**
  1. Output always re-enters the normal gates: `BUS_RE`, verb allowlist, dispatcher validation.
  2. No translator holds signing keys beyond its own actor's key.
  3. Failure mode is drop, never guess.
