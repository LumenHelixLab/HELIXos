# Security Policy

HELIXos includes a real security boundary — **Gate 2**, the hardened Wasm
execution adapter in [`helix_gate/`](helix_gate/). It ingests signed HX1
envelopes and executes untrusted Wasm in a capability-restricted, process-isolated
sandbox. Weaknesses in this boundary are the security issues we care most about.

## Supported

| Component | Security-supported |
|---|---|
| `helix_gate/` (Gate 2) | ✅ Yes — report issues privately |
| All other trees (kernel, IRC, babel, agents) | ❌ Not implemented; specification stubs |

## Reporting a vulnerability

**Do not open a public issue for a security vulnerability.**

Use GitHub's private vulnerability reporting for this repository
(**Security → Report a vulnerability**), or contact the maintainers privately.

Please include:

- the component and file(s) involved (e.g. `helix_gate/hx1/signature.py`);
- a description of the weakness and its impact on the trust boundary;
- a minimal reproduction — ideally a failing HX1 envelope or Wasm module and the
  observed vs. expected `GateResult` / lifecycle state.

## In scope (Gate 2)

- Any input that reaches `EXEC_COMPLETED` when it should have been rejected —
  signature/algorithm confusion, replay, expired/forged envelopes, digest
  mismatch, capability escalation, policy bypass.
- Sandbox escape or capability leakage: a guest gaining host authority beyond its
  signed manifest, evading fuel/epoch/memory/output limits, or resisting
  cancellation.
- Audit-integrity breaks: forging, reordering, or dropping events without
  `verify_chain()` detecting it.
- Reason-code / result paths that leak internal diagnostic detail to callers.

## Out of scope

- The unimplemented specification trees (they execute nothing).
- Findings that depend on the demo/test issuing keys (`helix_gate/testkit.py`);
  those private keys are fixtures, not production custody, which is a documented
  deferred seam (KMS/HSM).
