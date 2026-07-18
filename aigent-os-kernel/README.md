# aigent-os-kernel — Layer 1 (The Cognitive Engine, TEN²)

The reversible-state foundation. Handles the low-level "physics" of the system.
See [`docs/SPECIFICATION.md` §1](../docs/SPECIFICATION.md#layer-1--the-cognitive-engine-ten) and
[§6](../docs/SPECIFICATION.md#6-mathematical-sources--dependencies-reference-log).

## Modules

| File | Responsibility |
|---|---|
| `fsm_core.py` | 10-point finite reversible FSM over `X = R ∪ M`; generator `L = (2 3 4 6 5 8 7 9)`, involution `δ = L⁴`. NumPy GF(2). |
| `knot_api_wrapper.py` | Opaque AKASH C-binding (zero-copy) — braid signatures / Alexander-polynomial verification. |
| `error_correction.py` | 32.CUBIT Hamming [32, 26, 4] SECDED + extended Golay [24, 12, 8]. |

## Build order

1. `fsm_core.py` — establish the group action and reversibility invariants first.
2. `error_correction.py` — independent; can be built and unit-tested in parallel.
3. `knot_api_wrapper.py` — depends on a stable state representation from `fsm_core`.

## Open questions (must resolve before implementation)

- The generator `L = (2 3 4 6 5 8 7 9)` is an 8-element cycle on a 10-point set;
  the partition `X = R ∪ M` and fixed points of `δ = L⁴` need to be pinned down
  against the source paper before coding the transition table.
- "Projective Collapse to Node 7" (spec §4.3) requires a defined mapping from
  zero-divisor / error states to node 7 — currently unspecified numerically.
