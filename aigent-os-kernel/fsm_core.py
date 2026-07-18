"""TEN² kernel — 10-state finite reversible machine (Layer 1).

Implements the "physics" of the HELIXos substrate: a reversible state machine
acting on a partitioned 10-point set ``X = R ∪ M`` under the group
``G ≅ D₈ × ℤ₂``.

Spec: docs/SPECIFICATION.md §1 (Layer 1) and §6 (source paper).

Design constraints derived from the spec:

* Principal generator ``L = (2 3 4 6 5 8 7 9)`` in cycle notation, order 8.
* Involution ``δ = L⁴`` (so ``δ² = identity``).
* Every transition must be reversible: the transition relation is a bijection
  on ``X`` and each applied operation has an explicit inverse.

Nothing here is implemented yet — the transition table cannot be written until
the ``R``/``M`` partition and the numbering of ``X`` are pinned against the
source paper (see ``README.md`` open questions).
"""

from __future__ import annotations

# Principal generator in cycle notation, per spec §1. The points named here are
# the labels used in the source paper; the concrete 0..9 index mapping is TBD.
PRINCIPAL_GENERATOR_CYCLE: tuple[int, ...] = (2, 3, 4, 6, 5, 8, 7, 9)
GENERATOR_ORDER: int = 8

# Micro/meso temporal sync constants (spec §1, "Temporal Sync").
MICRO_CLOCK_TICKS: int = 840       # real-time computational steps
MESO_CLOCK_TICKS: int = 10_920     # I Ching macro-epochs

# Deterministic failure target for Projective Collapse (spec §4.3).
COLLAPSE_NODE: int = 7


class Ten2Kernel:
    """Reversible 10-point finite-state machine.

    The public contract (once implemented):

    * ``apply(op)``   — advance the state by a reversible operation.
    * ``inverse(op)`` — the operation that undoes ``apply(op)``.
    * ``collapse()``  — deterministic Projective Collapse to ``COLLAPSE_NODE``.
    """

    def __init__(self) -> None:
        raise NotImplementedError(
            "TEN² transition table is unspecified until the R/M partition and "
            "point numbering are resolved against the source paper. See "
            "aigent-os-kernel/README.md."
        )
