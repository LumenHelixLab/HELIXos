# HELIXos Security Gates

HELIXos hardens its trust boundaries in ordered **gates**. A gate is not "locked"
until its boundary is implemented for real and passes conformance + fault-injection
tests. This document records the gate model and the disposition of each gate.

## Gate model

| Gate | Boundary | Status |
|---|---|---|
| Gate 1 | HX1 envelope format + issuer signing model | Design |
| **Gate 2** | **Execution boundary — the hardened Wasm adapter** (`helix_gate/`) | **Implemented + tested** |
| Gate 3 | AKASH archive write/verify boundary (Akosh braid signatures) | Not started |

Gates are completed in order: opening a later gate before an earlier one is
locked would leave two unfinished trust boundaries instead of one.

## Gate 2 — Hardened Wasm Execution Adapter

Gate 2 turns an **authorized intent** into **executed code**. It is implemented
in the [`helix_gate/`](../helix_gate) package. See that package for the module
map; the pipeline is:

```
ingress -> decode -> schema-validate -> canonicalize -> verify signature
  -> issuer/audience -> time window -> replay (nonce/sequence)
  -> policy revision + capability manifest -> resolve module + verify digest
  -> authorize -> disposable Wasmtime worker -> signed audit event
```

### Reviewer disposition

An external review of an earlier *Draft 1* controller sketch approved the
architectural direction but **rejected the implementation**, with 13 findings.
Draft 1's fatal flaws: a stubbed signature check (`return True`), incomplete
envelope validation, no real Wasm isolation, synchronous (uncancellable)
execution, an in-memory dict masquerading as a registry, and plain-string
"audit events."

This implementation closes every finding. Each is mapped to the module that
addresses it and the test that exercises it in
[`tests/test_traceability.py`](../tests/test_traceability.py):

| # | Finding | Closed by |
|---|---|---|
| 1 | Signature verification was a placeholder | `hx1/canonical.py`, `hx1/signature.py` (Ed25519 over canonical bytes; keyring w/ revocation + algorithm-confusion guard) |
| 2 | HX1 validation incomplete | `validation.py` (full ordered pipeline) |
| 3 | No binding to a specific Wasm artifact | `module_resolver.py` (immutable `knot://` ref, sha256-verified) |
| 4 | "Physically trapped" overstated / not isolated | `sandbox/worker.py` (disposable process; no host authority beyond declared imports) |
| 5 | Cancellation could not work (synchronous) | `sandbox/controller.py` (process isolation; concurrent cancel + hard kill) |
| 6 | Resource controls absent | `policy.py` + `sandbox/worker.py` (fuel, epoch/wall, memory, table, stack, output caps) |
| 7 | Audit events unsigned / unstructured | `audit.py` (signed, hash-chained, append-only, structured) |
| 8 | Lifecycle not deterministic; state deleted | `lifecycle.py` + `registry.py` (explicit FSM; records retained) |
| 9 | Cancellation not idempotent | `registry.py` (terminal ops return their outcome, never `TOO_LATE`) |
| 10 | Verb model too broad | `policy.py` (only `EXECUTE_WASM_COMPONENT`) |
| 11 | Invalid signatures were not distinguished from unsafe failures | `errors.py` (`REJECTED_*` vs `FAILED_UNSAFE`) |
| 12 | Exception text could leak information | `results.py`/`errors.py` (stable reason codes; `detail` only to audit) |
| 13 | In-memory dict was not an authoritative registry | `registry.py` (durable SQLite, atomic CAS, distinct ids, restart recovery) |

### Accurate isolation claim

Core WebAssembly grants the guest **no host authority except capabilities the
embedder supplies through imports**. Gate 2 supplies none by default; the signed
capability manifest is the only source of authority, and undeclared imports are
denied both at policy time and (defense in depth) at instantiation time. For
hard containment the guest runs in a **disposable worker process**, so a runaway
or trapped guest can be terminated by the controller — there is no in-process
"kill a running Wasm call" primitive for embedded Wasmtime.

This is deliberately weaker language than "physically trapped / total isolation
/ kills the microVM instantly": those overstate what the code provides.

### Verify

```
pip install -r requirements.txt
pytest -q                      # 48 conformance + fault-injection tests
python -m helix_gate.demo      # live end-to-end + fault-injection tour
```
