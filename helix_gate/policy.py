"""Policy revision + capability-manifest evaluation (closes findings 2, 6, 10).

The verb model is deliberately narrow: Gate 2 authorizes exactly one operation,
``EXECUTE_WASM_COMPONENT``. Broad verbs like ``WRITE``/``REFACTOR`` are cognitive
intent decomposed *before* this boundary; they never reach it.

A signed capability manifest is honoured only within policy ceilings:

* every requested import must appear in ``allowed_imports`` — an undeclared or
  disallowed import is a capability escalation and is denied (finding 6);
* every output destination must appear in ``allowed_output_destinations``;
* every resource limit must be <= the policy ceiling for that limit.

"Deny by default": an empty allowlist denies everything of that kind.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import ReasonCode, ValidationRejection
from .hx1.schema import ALLOWED_OPERATION


@dataclass(frozen=True)
class Policy:
    revision: str
    allowed_imports: frozenset[str] = frozenset()
    allowed_output_destinations: frozenset[str] = frozenset()
    limit_ceilings: dict[str, int] = field(default_factory=dict)

    def evaluate(self, envelope) -> None:
        """Authorize ``envelope`` against this policy or raise ValidationRejection."""
        if envelope.policy_revision != self.revision:
            raise ValidationRejection(
                ReasonCode.REJECTED_POLICY_REVISION,
                f"envelope {envelope.policy_revision} != active {self.revision}",
            )
        if envelope.operation != ALLOWED_OPERATION:
            raise ValidationRejection(
                ReasonCode.REJECTED_OPERATION_NOT_ALLOWED,
                f"operation={envelope.operation!r}",
            )

        manifest = envelope.capability_manifest
        for imp in manifest["imports"]:
            if imp not in self.allowed_imports:
                raise ValidationRejection(
                    ReasonCode.REJECTED_CAPABILITY_ESCALATION,
                    f"import {imp!r} not permitted by policy",
                )
        for dest in manifest["output_destinations"]:
            if dest not in self.allowed_output_destinations:
                raise ValidationRejection(
                    ReasonCode.REJECTED_CAPABILITY_ESCALATION,
                    f"output destination {dest!r} not permitted by policy",
                )
        for name, requested in envelope.resource_limits.items():
            ceiling = self.limit_ceilings.get(name)
            if ceiling is not None and requested > ceiling:
                raise ValidationRejection(
                    ReasonCode.REJECTED_RESOURCE_LIMIT_EXCEEDS_POLICY,
                    f"{name}={requested} exceeds ceiling {ceiling}",
                )
