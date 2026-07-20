"""Root conftest — flat import namespace for HELIXos (SPEC.md §1).

Canonical version owned by Agent E (platform scaffolding, audit §7 item 7 /
AUD-H8). All six code directories are placed on ``sys.path`` so that tests
import modules by bare name, e.g. ``from signers import HMACSigner`` — module
filenames are unique across the repo by convention (SPEC §1).

pytest imports this file before collecting ``tests/``; keep it free of
third-party imports (stdlib only) so collection never fails on a bare
checkout.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_KERNEL = _ROOT / "aigent-os-kernel"

# The code directories of SPEC §1 (+ SPEC-M1 §1 akash), in stable order.
_CODE_DIRS = (
    _KERNEL / "src" / "runtime",       # ten_squared_fsm.py
    _KERNEL / "src" / "memory",        # journal.py, epochs.py
    _KERNEL / "src" / "BABEL",         # dispatcher.py
    _KERNEL / "src" / "akash",         # braid.py (SPEC-M1 §1)
    _KERNEL / "KNOTstore_bin",         # signers.py, knotcore_sim.py, KNOT_API_WRAPPER.py
    _KERNEL / "KNOTstore_bin" / "sidecar",  # rpc_protocol.py, sidecar_server.py, sidecar_client.py
    _KERNEL / "orchestrator",          # possession.py
)

for _dir in reversed(_CODE_DIRS):
    # reversed() so that after repeated insert(0) the final sys.path order
    # matches the declaration order above. Guarded for idempotence.
    _path = str(_dir)
    if _path not in sys.path:
        sys.path.insert(0, _path)
