# helix_gate — Gate 2: Hardened Wasm Execution Adapter

The HELIXos execution trust boundary. Turns an **authorized intent** (a signed
HX1 envelope) into **executed code** (a Wasm module run in a disposable,
capability-restricted sandbox), emitting a signed audit trail. This is the
concrete execution boundary of Layer 1 — see [`../docs/GATES.md`](../docs/GATES.md)
for the gate model and the reviewer disposition it satisfies.

## Pipeline

```
ingress -> decode -> schema-validate -> canonicalize -> verify signature
  -> issuer/audience -> time window -> replay (nonce/sequence)
  -> policy revision + capability manifest -> resolve module + verify digest
  -> authorize -> disposable Wasmtime worker -> signed audit event
```

## Module map

| Module | Responsibility |
|---|---|
| `hx1/schema.py` | HX1 v1 envelope + strict decode |
| `hx1/canonical.py` | RFC 8785-profile canonical bytes (signature input) |
| `hx1/signature.py` | Ed25519 verify + keyring (revocation, algorithm-confusion guard) |
| `validation.py` | The ordered validation pipeline |
| `replay.py` | Durable nonce + monotonic-sequence store |
| `policy.py` | Policy revision, verb allowlist, capability manifest, resource ceilings |
| `module_resolver.py` | `knot://` resolution + sha256 digest binding (AKASH seam) |
| `sandbox/worker.py` | Child-process Wasmtime execution under resource limits |
| `sandbox/controller.py` | Process supervision + interruptible cancellation |
| `lifecycle.py` | Deterministic state machine (retained records) |
| `registry.py` | Durable SQLite registry: atomic CAS, recovery, idempotent cancel |
| `audit.py` | Signed, hash-chained, append-only structured audit log |
| `results.py` / `errors.py` | Structured results + stable reason-code taxonomy |
| `adapter.py` | `ExecutionGate` — the orchestrator |
| `testkit.py` | Issuers, sample modules, and harness builders (tests + demo) |
| `demo.py` | Runnable end-to-end + fault-injection tour |

## Usage

```python
import json
from helix_gate import testkit as tk

h = tk.build_harness("/tmp/gate")
wasm = tk.publish(h.store, "answer")          # a sample Wasm module
env = h.issuer.envelope(wasm=wasm)            # a signed HX1 envelope
result = h.gate.submit(json.dumps(env), owner="krishna")
print(result.outcome, result.reason)          # COMPLETED EXEC_COMPLETED
assert h.audit.verify_chain()
```

In production, replace `testkit` issuers with real HX1 producers, back the
`ModuleStore` with the AKASH/`knot_api_wrapper` binding, and move signing keys
into a KMS/HSM (a deferred seam).

## Verify

```
pip install -r ../requirements.txt
pytest -q                 # 48 conformance + fault-injection tests
python -m helix_gate.demo
```
