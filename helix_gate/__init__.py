"""helix_gate — HELIXos Gate 2: the hardened Wasm execution adapter.

The trust boundary that turns an *authorized intent* into *executed code*:

    ingress -> decode -> schema-validate -> canonicalize -> verify signature
      -> issuer/audience -> time window -> nonce/sequence (replay)
      -> policy revision -> resolve module + verify digest
      -> capability manifest -> authorize operation
      -> sandbox registry -> disposable Wasmtime worker
      -> canonicalize result -> signed append-only audit event

Nothing outside a signed HX1 envelope's declared capability manifest is granted
to guest code: the Wasm guest receives no host authority except imports the
adapter explicitly supplies. See ``docs/GATES.md``.
"""

from __future__ import annotations

from .adapter import ExecutionGate
from .errors import GateOutcome, ReasonCode
from .results import GateResult

__all__ = ["ExecutionGate", "GateResult", "GateOutcome", "ReasonCode"]

__version__ = "0.1.0"
