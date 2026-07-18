"""AKASH / KNOTstore binding — Layer 3 archive access from the kernel side.

Persists verified solutions as **Akosh**: immutable topological braid signatures
verified via Alexander polynomials, rather than raw data.

Spec: docs/SPECIFICATION.md §1 (Layer 3).

This is the zero-copy binding boundary. The intended contract:

* ``store(braid)``  — commit a braid signature; returns its Akosh id.
* ``verify(akosh)`` — recompute the Alexander polynomial and confirm the
  signature is unchanged (zero-trust read).
* ``restore()``     — return the last stable AKASH state (used by Projective
  Collapse, spec §4.3).

Not implemented: depends on a stable state representation from ``fsm_core`` and
on the AKASH native library, which is not yet vendored into this repository.
"""

from __future__ import annotations


class KnotStore:
    """Opaque handle to the AKASH topological long-term memory store."""

    def __init__(self) -> None:
        raise NotImplementedError(
            "AKASH native binding is not yet available. Blocked on a stable "
            "kernel state representation (aigent-os-kernel/fsm_core.py)."
        )
