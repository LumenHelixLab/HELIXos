<!-- Thanks for contributing to HELIXos. Keep claims truthful: mark aspirations as
aspirations, and back any number with a reproduction path. -->

## Summary

<!-- What does this change do, and why? -->

## Area

- [ ] `helix_gate/` (Gate 2 — implemented boundary)
- [ ] Tests
- [ ] Documentation / specification
- [ ] A specification stub → implementation (which component? __________)

## Truthfulness check

- [ ] No component is described as "working" unless it is implemented **and** tested.
- [ ] Any benchmark/metric included has a reproduction command (or none is included).

## Verification

<!-- Paste the output or confirm you ran it. -->

- [ ] `pytest -q` passes
- [ ] `python -m helix_gate.demo` runs clean (if the change touches `helix_gate/`)
- [ ] New behavior has conformance **and** fault-injection tests (for `helix_gate/` changes)

## Breaking changes

<!-- Does this change any public API, the HX1 envelope schema, or a reason code? -->
