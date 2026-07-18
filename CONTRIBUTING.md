# Contributing to HELIXos

HELIXos is a large architectural specification with one boundary implemented for
real (**Gate 2**, the hardened Wasm execution adapter in `helix_gate/`). That
split shapes how we take contributions: the spec is a map, and we turn regions
of it into verified code one gate at a time.

## The prime directive: truth over impressiveness

This repository wraps ambitious, mythologically-framed ideas around a small
amount of genuinely working code. The line between the two must never blur.

- **Never claim something works that hasn't been implemented and tested.** Stubs
  raise `NotImplementedError` and say so. Documented aspirations are marked as
  aspirations.
- **No fabricated numbers.** Benchmarks, latencies, throughput, and "verified
  against X" claims require a reproduction path or they don't ship.
- If you implement part of the spec, the docs for that part move from
  "specified" to "implemented + tested" — and not before.

## Dev setup

```bash
git clone https://github.com/LumenHelixLab/HELIXos
cd HELIXos
pip install -r requirements.txt
```

## Verify before you open a PR

```bash
pytest -q                    # the Gate 2 conformance + fault-injection suite
python -m helix_gate.demo    # live end-to-end + fault-injection tour
```

CI runs exactly this on every PR (`.github/workflows/ci.yml`).

## Branch & commit conventions

- Branch from `main`; use a descriptive branch name.
- Write descriptive commit subjects in the imperative mood ("Add replay store",
  "Fix epoch watchdog race"), with a body explaining *why* when it isn't obvious.
- Match the style already present in the files you touch (comment density,
  naming, docstrings).

## What we welcome

- **Implementing a gate or a specified component** — with a conformance +
  fault-injection test suite that mirrors `tests/` and a traceability note for
  any external review findings it closes (see `docs/GATES.md`).
- **Hardening `helix_gate/`** — new fault-injection cases, tighter isolation,
  clearer reason codes, performance work with reproducible measurements.
- **Documentation that increases precision** — pinning down an under-specified
  part of `docs/SPECIFICATION.md` against a real source.

## What we decline

- Code that claims to implement kernel math, error correction, or agent behavior
  without a verifiable basis (e.g. the source paper) and without tests.
- Marketing-style additions: manufactured metrics, unverifiable claims, badges
  for tooling that doesn't exist.
- Large refactors of `helix_gate/` that trade its auditability for cleverness.

## Reporting security issues

The Gate 2 trust boundary is security-sensitive. Please follow
[`SECURITY.md`](SECURITY.md) rather than opening a public issue for anything that
could weaken the execution boundary.
