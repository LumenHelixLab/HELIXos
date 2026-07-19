# HELIXos Per-Layer Latency Budgets

| | |
|---|---|
| **Status** | Binding for M0 and all later milestones |
| **Fixes** | AUD-C7 (`HELIXos_Handoff_Audit.md` §3) — the v2.0 blanket "< 1ms" requirement |
| **Governs** | `ten_squared_fsm.py` (SPEC §3.9), the Triangulated Bus wire path (SPEC §2), the knotcore sidecar (SPEC §3.4, ADR-002), the IRC control plane (ADR-003) |
| **Enforced by** | `benchmark()` in `ten_squared_fsm.py` + CI gate (§4 of this document) |

---

## 1. Why the original "<1ms" requirement was unsatisfiable

The v2.0 handoff (L103) imposed a single latency requirement — *"the FSM stays within the
TEN-SQUARED latency requirement (< 1ms)"* — while simultaneously routing agent-to-agent
messages over IRC, a line-oriented text protocol carried on TCP, and mandating "avoid
standard serialization" via zero-copy memory. One number was applied to two physically
different layers, and it is violated by construction at the transport layer.

### 1.1 Quantified cost of one agent→agent bus message (localhost, idle system)

| # | Stage | Typical cost | Notes |
|---|---|---|---|
| 1 | Serialize bus line (format `[ ptr | verb | #tag ]`, canonical encoding, MAC/sign) | 5–20 µs | f-string + HMAC-SHA256 over <1 KiB; Ed25519 sign adds ~20–60 µs |
| 2 | `write()`/`send()` syscall | 5–15 µs | Small payload, loopback, no disk involved |
| 3 | Loopback TCP delivery (kernel hand-off to IRCd) | 20–60 µs | Loopback MTU path; no Nagle delay for single small writes |
| 4 | IRCd wakeup → parse → fan-out | 100–500 µs idle; **1–10 ms under queueing** | Event-loop wakeup, line parse, per-member copy; flood-control and output-queueing dominate under burst |
| 5 | Recipient `read()` + parse (regex, envelope JSON, verify) | 20–100 µs | `BUS_RE.fullmatch` + `json.loads` + MAC compare |
| | **Best-case sum** | **≈ 150 µs** | Stages 1–5 at minima |
| | **Typical sum** | **≈ 0.7–1.3 ms** | The *median starts at the budget* |

Queuing, not arithmetic, sets the tail. Adding the two dominant tail contributors:

| Tail contributor | Magnitude | Effect |
|---|---|---|
| IRCd output queueing / flood control under burst | 1–10 ms | p95 moves to 2–10 ms |
| CPython GC pause (gen-2 collection on either endpoint) | 1–100 ms | p99 moves to 10–50 ms |

**Realistic profile over localhost IRC: p50 0.5–2 ms, p95 2–10 ms, p99 10–50 ms.**
A blanket "<1ms" is therefore unachievable at the median, and exceeded 10–50× at p99.
A requirement that is always violated will be silently ignored — which is worse than no
requirement, because it hides real regressions behind a dead gate.

### 1.2 The zero-copy contradiction

The same sentence that imposed <1ms mandated zero-copy ("avoid standard serialization").
Zero-copy is **physically impossible across a TCP socket**: the moment state crosses the
bus it must be serialized into a byte stream. The mandate optimized the one layer
(in-process state access) that was never at risk, while forbidding the operation the
transport layer cannot exist without. §5 re-scopes zero-copy to in-process use only and
makes serialization an explicit, budgeted cost at every transport boundary.

### 1.3 The system is LLM-latency-dominated

Every agent step in the Quaternity involves one or more model calls costing
**100 ms–10 s**. Against that, a 1–5 ms bus message is noise (<1% of step time). The
<1ms figure was solving the wrong problem: the bus is not, and cannot become, the
bottleneck at M1–M3 scale. Budgets below are set where they protect real behavior
(detecting a pathological FSM implementation; detecting a wedged daemon), not where they
imitate a hard-real-time system HELIXos is not.

---

## 2. The corrected two-layer budget

The single requirement is replaced by two independently measured layer budgets.

### LAYER 1 — In-process FSM transition

| Property | Value |
|---|---|
| Scope | One `TenSquaredFSM.transition(event)` call (SPEC §3.9) |
| Budget | **p99 < 1000 µs** per transition (hard gate); expected p50 < 5 µs with the materialized tuple-of-tuples table |
| Measurement | `benchmark()` in `ten_squared_fsm.py` (method in §3) |
| Rationale | Transition is table lookup + index arithmetic; anything near 1 ms indicates allocation, I/O, or locking leaked onto the hot path. The 1000 µs gate catches exactly that class of regression without flapping on CI-runner jitter. |

### LAYER 2 — Bus delivery (transport boundary)

| Property | Value |
|---|---|
| Scope | One bus line, sender `write()` to recipient verified parse, localhost, M1 scale (4 agents + operators, ≤ ~10² msgs/s) |
| Budget | **p99 < 10 ms** |
| Measurement | Integration soak (§4.3) |
| Rationale | IRC at this scale delivers p50 ≈ 0.5–2 ms; 10 ms p99 leaves ~5× headroom for queueing while still failing fast if the daemon wedges. Revisit only if the data-plane triggers in ADR-003 §6 fire (dozens of agents or ~1k msgs/s). |

The knotcore sidecar (ADR-002) sits between the layers: it is a transport boundary
(Unix socket, serialized frames) with a per-call deadline of 2.0 s (`KnotClient`
default) — an availability bound, not a latency budget. Its latency is not gated in M0;
the circuit breaker, not a percentile, is the enforcement mechanism.

### Context figure (non-binding)

LLM agent step: 100 ms–10 s. Both layer budgets are 3–6 orders of magnitude below the
dominant term. Latency work beyond these gates is wasted effort; spend it on
correctness and failure semantics (ADR-001).

---

## 3. Measurement method (Layer 1)

`benchmark(iterations: int = 100_000) -> dict` in `ten_squared_fsm.py` is the reference
measurement. It returns `{"p50_us", "p99_us", "p999_us"}` (microseconds per transition).
Method requirements, all mandatory:

1. **Clock:** `time.perf_counter_ns()` — monotonic, nanosecond resolution. Wall clocks
   (`time.time`) are forbidden for measurement.
2. **GC disabled during measurement:** `gc.disable()` before the timed loop,
   `gc.enable()` after (in `finally`). This measures the transition itself, not the
   collector; GC pauses are a Layer-2 tail concern (§1.1) and are addressed in
   production by keeping per-transition allocation at zero, not by hiding them in the
   benchmark. (Pre-froze long-lived tables via `gc.freeze()` at init where available.)
3. **Pre-allocated inputs:** the event list (`"E0".."E9"` names, interned at init) is
   built once *before* the timed loop. No allocation inside the timed region — the
   benchmark must obey the same hot-path rules it enforces (§5).
4. **Warmup:** ≥ 1,000 unmeasured transitions before the timed loop (branch predictor,
   code-path caching).
5. **Percentiles, hdrhistogram-style:** record every per-iteration latency into a
   fixed-resolution integer histogram (1 µs buckets, capped at a max trackable value
   with an overflow bucket), then derive p50/p99/p999 from bucket counts — never from
   mean/min/max, and never from a reservoir sample. A sorted-array percentile over all
   100k samples is an acceptable equivalent (identical result at this n); either way,
   the full distribution is retained, not aggregated away.
6. **Deterministic workload:** events cycle `E0..E9` uniformly so every run exercises
   the same 10-event mix across all 100 states.

---

## 4. CI enforcement

### 4.1 Layer 1 gate (every pull request)

CI job runs: `pytest tests/ -k fsm_benchmark` (or equivalent), which calls
`benchmark(100_000)` on the CI runner and asserts:

| Check | Gate | Failure meaning |
|---|---|---|
| `p99_us < 1000` | **Hard fail** | Hot-path regression: allocation, I/O, lock, or dict lookup introduced into `transition()` |
| `p999_us < 5000` | **Hard fail** | Tail regression (scheduler jitter is tolerated; 5 ms at p999 is not) |
| `p50_us` trend | Artifact only | Benchmark JSON uploaded as CI artifact; >20% p50 regression vs. the stored `main` baseline triggers review but does not block merge |

CI runners are shared, noisy vCPUs: the 1000 µs gate is set ~100× above the expected
p99 of a conforming implementation (single-digit µs) precisely so the gate fails on
defects, not on neighbor noise. Do not tighten the gate to "expected" values; a gate
that flaps will be disabled, and a disabled gate is the AUD-C7 failure mode recurring.

### 4.2 Where the numbers live

Each CI run stores `{"p50_us","p99_us","p999_us", iterations, python_version, cpu_count}`
as a build artifact. The `main`-branch artifact is the regression baseline for §4.1.

### 4.3 Layer 2 gate (integration, pre-merge + nightly)

An integration test starts the local IRCd (ADR-003) and two bus clients, exchanges
N = 1,000 bus lines, records send→verified-parse latency per message with the same
histogram discipline as §3, and asserts **p99 < 10 ms**. Nightly soak extends this to
10 minutes under burst load to exercise the flood-control path (§1.1 stage 4).

### 4.4 Non-goals

No CI gate is placed on: sidecar call latency (circuit-breaker governed, ADR-002),
LLM call latency (external, 100 ms–10 s), or cross-machine delivery (M2+ concern;
budgets here are localhost).

---

## 5. Hot-path rules (binding)

1. **No logging I/O on the hot path.** `transition()` never touches `logging`, files,
   sockets, or `print`. Audit/observability records are appended to a pre-allocated
   ring buffer and emitted off-path by the caller (the BABEL dispatcher audits; the FSM
   does not).
2. **No per-transition allocation.** The transition table is materialized at `__init__`
   as a tuple-of-tuples (SPEC §3.9); state and event names are interned; `transition()`
   performs index arithmetic and tuple indexing only — no dict lookups, no string
   formatting, no object construction.
3. **Zero-copy applies ONLY in-process.** Within one address space, state access avoids
   copies where the data structure allows it. The v2.0 "avoid standard serialization"
   mandate is hereby rescoped to this layer and this layer only.
4. **Serialization is mandatory at transport boundaries.** Any state crossing a process
   or machine boundary is serialized exactly once at the boundary, in the canonical
   encoding of SPEC §2 (bus lines) or the newline-delimited JSON frames of SPEC §3.4
   (sidecar). Serialization cost is charged to the Layer 2 budget, never to Layer 1.

---

## 6. Summary

| Layer | Budget | Measured by | CI gate |
|---|---|---|---|
| L1 in-process FSM transition | p99 < 1000 µs, p999 < 5000 µs | `benchmark()` — perf_counter_ns, GC off, pre-allocated events, histogram percentiles | Hard fail per PR |
| L2 bus delivery, localhost | p99 < 10 ms | Integration soak, 1,000 msgs | Hard fail per PR + nightly |
| LLM agent step (context) | 100 ms–10 s | — | Ungated (external) |
