# ADR-002: knotcore Runs Out-of-Process Behind a Unix-Socket RPC Sidecar

| | |
|---|---|
| **Status** | Accepted |
| **Date** | M0 hardening pass |
| **Audit finding(s) resolved** | **AUD-H6** (blackbox `knotcore.so` in-process, unversioned, untestable, M1-gating); contributes to AUD-M2 (ownership boundary), AUD-H4 (silent failure modes) |
| **Governs** | `rpc_protocol.py`, `sidecar_server.py`, `sidecar_client.py` (SPEC §3.4), `knotcore_sim.py` (SPEC §3.2), `KNOT_API_WRAPPER.py` (SPEC §3.3), `tests/vectors/golden_vectors.json` |

---

## 1. Context

The v2.0 design linked the proprietary `knotcore.so` **in-process** via FFI and wrapped
it in `KNOT_API_WRAPPER.py`, a file marked "Do not modify" (v2.0 L26). The audit found
this combination to be one of the highest-risk elements in the program (AUD-H6):

- **An FFI segfault is an uncatchable host-process kill** — it takes down whichever
  agent loaded the library, including a LangGraph "God" mid-write to the ledger.
- The binary is **unversioned** (no ABI handshake, no load-time checksum), **untestable**
  (no mock, no contract tests, no golden vectors), and has **no timeout, circuit
  breaker, or health check**.
- It is **M1-gating**: the milestone cannot ship without it, yet there is no spike, no
  soak, and no vendor escrow/SLA.
- The **"Do not modify"** constraint freezes the wrapper that contains AUD-C1 — the
  broken "system firewall" is the one component engineers were forbidden to fix. The
  audit's fix also requires a **new binding, `fetch_payload(ptr)`**, which is impossible
  to add under a frozen interface.

Additionally, the ownership boundary was self-contradictory (AUD-M2): the wrapper was
simultaneously vendor-supplied "do not modify" blackbox and the M1 artifact engineers
were told to build.

## 2. Decision

### 2.1 Out-of-process sidecar

**The proprietary knotcore runs out of its consumers' address space, behind a
Unix-socket RPC sidecar**, implemented as three modules per SPEC §3.4:

| Module | Responsibility |
|---|---|
| `rpc_protocol.py` | Newline-delimited JSON frames: `{"id":int,"method":str,"params":dict}` → `{"id":int,"result":...}` or `{"id":int,"error":str}`; `encode_request`/`encode_response`/`parse_frame`; **1 MiB frame cap** |
| `sidecar_server.py` | Unix-socket server (default `$HELIXOS_SIDECAR_SOCKET`, else `/tmp/helixos-knotcore.sock`), one thread per connection, wraps `knotcore_sim` today and the real `.so` later **behind the same ABI**; methods `store_instruction`, `fetch_payload`, `verify_cauldron_phase`, `health`, `abi_version`; graceful SIGTERM; `main()` entry |
| `sidecar_client.py` | `KnotClient(socket_path, timeout=2.0, breaker_threshold=5, breaker_reset_s=30)`; typed failures via `class Unavailable(RuntimeError)` |

### 2.2 Failure engineering (the point of the exercise)

| Mechanism | Specification |
|---|---|
| **Supervisor restart** | The sidecar runs under the process supervisor (systemd unit in `infra/systemd/`); a crash or hang triggers automatic restart with backoff. A dead vendor process no longer implies a dead agent |
| **Per-call deadlines** | Every client call carries a socket timeout (default 2.0 s). A hung `.so` produces a typed `Unavailable`, never an unbounded block in an agent loop |
| **Circuit breaker** | After **5 consecutive errors** the breaker opens; calls fail fast with `Unavailable` for **30 s**, then half-open to probe recovery. Vendor failure degrades the system to a defined state instead of cascading |
| **ABI handshake** | `health()` returns `{"status":"ok","abi_version":1}`; the client checks `abi_version` at connect and refuses mismatches. Loading an incompatible binary is a boot-time error, not a runtime mystery |
| **Health endpoint** | `health` is exercised by the supervisor and by CI smoke tests independent of payload traffic |

Under ADR-001 §2.4, sidecar failures are classified `Unavailable` → circuit-break/retry,
**never** a Projective Collapse trigger.

### 2.3 Testability without the binary

- **Reference simulator:** `knotcore_sim.py` (SPEC §3.2) implements the full ABI in
  pure Python — 64-bit write-once slots with generation counters, `StoreFull` on
  exhaustion, `bump_generation` invalidation. The sidecar server wraps the simulator by
  default, so **CI runs the entire stack with no proprietary artifact present**.
- **Contract tests:** one test suite runs against both `SimBackend` and `SidecarBackend`
  (SPEC §3.3), so the simulator and the socket path cannot drift apart silently.
- **Golden vectors:** `tests/vectors/golden_vectors.json` pins known
  (payload, key, ptr, verb, phase) → (bus line, tag, verify verdict) tuples. The real
  binary, when present, must reproduce the vectors bit-for-bit; the vectors are the
  executable definition of ABI compatibility.

### 2.4 Ownership: "do not modify" is rescinded

**The project owns the adapter interface.** `KNOT_API_WRAPPER.py` and the sidecar are
HELIXos code, modifiable like any other. The vendor's frozen surface is reduced to the
smallest possible ABI — `store_instruction`, `fetch_payload`, `verify_cauldron_phase` —
accessed only inside `sidecar_server.py`. The `fetch_payload` binding required by the
audit §6 fix is hereby added to that ABI as a contractual requirement on the vendor.

### 2.5 M1 exit gate (vendor risk retirement)

Before M1 can exit with the real binary in place:

1. **Escrow/SLA:** source-escrow agreement and support SLA executed with the vendor,
   so the M1-gating dependency is survivable as a business matter, not just a
   technical one.
2. **24 h soak:** the sidecar + real `.so` runs 24 h under representative load with
   zero crashes, zero memory-growth trend, and golden-vector agreement throughout.
3. **Fault-injection gate:** chaos run kills the sidecar mid-call, restarts it, and
   feeds it garbage frames; expected behavior — agents observe typed `Unavailable`,
   the breaker opens and recovers, no agent process dies, no journal corruption
   (ADR-001 chain intact).

## 3. Consequences

### 3.1 Positive

- **Vendor crash = typed `Unavailable`, not a dead host.** The worst case moves from
  "uncatchable segfault kills an agent (possibly a ledger-owning God)" to "an exception
  the agent loop already knows how to classify" (ADR-001 §2.4).
- **CI is vendor-independent**: simulator + contract tests + golden vectors give full
  coverage without the binary; the binary is validated against the same suite when
  present.
- **Deadlines and the breaker bound the blast radius** of hangs and brownouts.
- **ABI handshake converts version skew into a boot error** — fail fast, in the open.
- **AUD-C1 is unfrozen**: with the adapter owned by the project, the corrected
  fail-closed verifier (audit §6.1) can actually ship.
- **One ABI, two implementations**: the real `.so` slots into `sidecar_server.py`
  without touching any consumer — consumers only ever see `KnotBackend`
  (`SimBackend`/`SidecarBackend`, SPEC §3.3).
- **Escrow/SLA + soak + fault injection retire the M1-gating risk** on evidence rather
  than optimism.

### 3.2 Negative

- **Extra hop latency**: one Unix-socket round trip (~tens of µs) plus serialization
  per call, versus a direct FFI call. Accepted: sidecar calls are availability-governed
  (2.0 s deadline), not latency-gated, and the system is LLM-latency-dominated
  (docs/latency-budgets.md §1.3).
- **A new process to operate**: supervisor unit, socket lifecycle, log capture —
  mitigated by the `infra/` substrate workstream (AUD-H8).
- **Serialization boundary must be disciplined**: frames are newline-delimited JSON
  (stdlib-only constraint); the 1 MiB cap means oversized payloads are explicit errors,
  requiring the payload-size limits already noted in AUD-L2 to be enforced at the
  wrapper.
- **Simulator fidelity risk**: if the real binary's semantics diverge from
  `knotcore_sim.py` in ways the golden vectors don't capture, integration bugs surface
  late. Mitigated by the 24 h soak + fault-injection gate (§2.5) and by growing the
  vector set whenever a discrepancy is found.
- **Thread-per-connection server** is simple but caps sidecar concurrency; acceptable
  at 4-agent scale, revisited if the ADR-003 §6 envelope is approached.

## 4. Alternatives considered

| Alternative | Why rejected |
|---|---|
| **Keep in-process FFI, add careful signal handling** | A segfault inside a `.so` cannot be caught or sandboxed from within the same process; no amount of Python discipline changes memory-unsafety across FFI. This is the AUD-H6 status quo |
| **Replace the vendor store with an in-house store** | The KNOTstore topological archive is the project's chosen differentiator and the M1 deliverable's anchor; writing it off in M0 discards the product thesis rather than hardening it |
| **gRPC / HTTP sidecar** | Requires third-party stacks (violates the stdlib-only M0 constraint), adds framing/TLS complexity for a same-host socket; newline-delimited JSON over a Unix socket is inspectable with `socat` and testable with `json` |
| **Shared-memory (mmap) channel** | Reintroduces torn-read/synchronization problems (AUD-M5) and lets vendor memory corruption reach consumer address spaces again — the exact failure mode being removed |
| **Do nothing; rely on the "do not modify" wrapper** | Freezes AUD-C1 in place and blocks the required `fetch_payload` binding; rejected by the audit outright |

## 5. Audit finding(s) resolved

- **AUD-H6** — resolved in full: out-of-process sidecar (§2.1), supervisor + deadlines
  + breaker + handshake + health (§2.2), simulator/contract tests/golden vectors
  (§2.3), ownership of the adapter (§2.4), escrow/SLA + soak + fault-injection M1 gate
  (§2.5).
- **AUD-M2** (ownership boundary) — vendor surface reduced to the three-function ABI;
  everything else is project-owned (§2.4).
- **AUD-H4** (silent failure modes) — every sidecar failure is a typed, logged
  exception or a framed `error` response; nothing fails silently across the boundary.
