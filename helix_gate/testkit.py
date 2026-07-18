"""Shared builders for tests and the demo: issuers, sample modules, gates.

Not part of the runtime trust boundary — this is issuing-side + fixture code
(it holds private keys and mints envelopes), kept in one place so tests and
``demo.py`` agree on the sample corpus.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass, field

import wasmtime
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .adapter import ExecutionGate
from .audit import AuditLog
from .hx1.schema import ALLOWED_OPERATION, HX1_SCHEMA_VERSION
from .hx1.signature import Key, KeyRing, KeyStatus, sign_envelope
from .module_resolver import LocalModuleStore
from .policy import Policy
from .registry import Registry
from .replay import ReplayStore

DEFAULT_AUDIENCE = "gate://ten2"
DEFAULT_POLICY_REVISION = "policy-2026-01"
# Deterministic base time so envelope validity windows line up with the gate
# clock in tests/demo. Envelopes default to issued_at == CLOCK_NOW; the gate's
# clock reads a few seconds later, inside the window.
CLOCK_NOW = 1_000_000

# --- sample WAT modules ----------------------------------------------------
WAT = {
    # returns 42, no output
    "answer": '(module (func (export "execute") (result i32) i32.const 42))',
    # exports memory + output_ptr/len returning "HELIX-OK"
    "echo": (
        '(module (memory (export "memory") 1) (data (i32.const 0) "HELIX-OK")'
        ' (func (export "execute") (result i32) i32.const 0)'
        ' (func (export "output_ptr") (result i32) i32.const 0)'
        ' (func (export "output_len") (result i32) i32.const 8))'
    ),
    # infinite loop (for cancel / timeout / fuel)
    "spin": '(module (func (export "execute") (result i32) (loop br 0) i32.const 0))',
    # out-of-bounds load -> guest trap
    "oob": (
        '(module (memory 1) (func (export "execute") (result i32)'
        ' i32.const 1000000 i32.load))'
    ),
    # requires an undeclared host import (defense-in-depth: fails to instantiate)
    "needs_import": (
        '(module (import "env" "f" (func))'
        ' (func (export "execute") (result i32) call 0 i32.const 0))'
    ),
    # output_len (1000) exceeds a small output_bytes cap -> OUTPUT_OVERFLOW
    "big_output": (
        '(module (memory (export "memory") 1)'
        ' (func (export "execute") (result i32) i32.const 0)'
        ' (func (export "output_ptr") (result i32) i32.const 0)'
        ' (func (export "output_len") (result i32) i32.const 1000))'
    ),
}


def compile_wat(name: str) -> bytes:
    return wasmtime.wat2wasm(WAT[name])


def default_limits(**overrides) -> dict:
    limits = {
        "fuel": 10_000_000,
        "wall_ms": 3000,
        "memory_bytes": 65536,
        "table_elems": 0,
        "stack_bytes": 262144,
        "output_bytes": 64,
        "host_calls": 0,
    }
    limits.update(overrides)
    return limits


def default_policy(**overrides) -> Policy:
    kwargs = dict(
        revision=DEFAULT_POLICY_REVISION,
        allowed_imports=frozenset(),
        allowed_output_destinations=frozenset({"obsidian://hud"}),
        limit_ceilings={
            "fuel": 100_000_000,
            "wall_ms": 10_000,
            "memory_bytes": 16 * 65536,
            "table_elems": 1024,
            "stack_bytes": 1_048_576,
            "output_bytes": 4096,
            "host_calls": 0,
        },
    )
    kwargs.update(overrides)
    return Policy(**kwargs)


@dataclass
class Issuer:
    name: str
    private_key: Ed25519PrivateKey = field(default_factory=Ed25519PrivateKey.generate)
    key_id: str = field(default_factory=lambda: "key-" + uuid.uuid4().hex[:10])
    _seq: int = 0

    def key(self, status: KeyStatus = KeyStatus.ACTIVE) -> Key:
        return Key(self.key_id, self.name, self.private_key.public_key(), status)

    def next_sequence(self) -> int:
        self._seq += 1
        return self._seq

    def envelope(self, *, wasm: bytes, entrypoint: str = "execute",
                 audience: str = DEFAULT_AUDIENCE,
                 policy_revision: str = DEFAULT_POLICY_REVISION,
                 limits: dict | None = None, manifest_imports=None,
                 output_destinations=None, now: int = CLOCK_NOW,
                 lifetime: int = 3600, nonce: str | None = None,
                 sequence: int | None = None, key_id: str | None = None,
                 operation: str = ALLOWED_OPERATION,
                 interface_version: str = "helix:execution@1.0.0",
                 artifact_ref: str | None = None) -> dict:
        digest = hashlib.sha256(wasm).hexdigest()
        signed = {
            "schema_version": HX1_SCHEMA_VERSION,
            "issuer": self.name,
            "audience": audience,
            "issued_at": now,
            "not_before": now,
            "expires_at": now + lifetime,
            "nonce": nonce or ("nonce-" + uuid.uuid4().hex),
            "sequence": sequence if sequence is not None else self.next_sequence(),
            "policy_revision": policy_revision,
            "operation": operation,
            "target": "#t-exec",
            "module": {
                "artifact_ref": artifact_ref or ("knot://" + digest),
                "sha256": digest,
                "runtime_profile": "helix-wasm-v1",
                "entrypoint": entrypoint,
                "interface_version": interface_version,
            },
            "capability_manifest": {
                "imports": list(manifest_imports or []),
                "input_objects": [],
                "output_destinations": list(output_destinations or []),
                "resource_limits": limits or default_limits(),
            },
        }
        return sign_envelope(signed, self.private_key, key_id or self.key_id)


@dataclass
class Harness:
    gate: ExecutionGate
    issuer: Issuer
    store: LocalModuleStore
    keyring: KeyRing
    audit: AuditLog


def build_harness(tmp_dir: str, clock_now: int = CLOCK_NOW + 5,
                  policy: Policy | None = None) -> Harness:
    """A ready-to-use gate + issuer + module store on ``tmp_dir``.

    The gate clock is fixed at ``clock_now`` (default: just inside the default
    envelope validity window) so tests are deterministic.
    """
    store = LocalModuleStore(os.path.join(tmp_dir, "modules"))
    issuer = Issuer(name="issuer://alpha")
    keyring = KeyRing([issuer.key()])
    audit = AuditLog(os.path.join(tmp_dir, "audit.db"),
                     Ed25519PrivateKey.generate(), "audit-key-1")
    gate = ExecutionGate(
        keyring=keyring,
        policy=policy or default_policy(),
        module_store=store,
        audience=DEFAULT_AUDIENCE,
        replay=ReplayStore(os.path.join(tmp_dir, "replay.db")),
        registry=Registry(os.path.join(tmp_dir, "registry.db")),
        audit=audit,
        clock=lambda: clock_now,
    )
    return Harness(gate=gate, issuer=issuer, store=store, keyring=keyring, audit=audit)


def publish(store: LocalModuleStore, name: str) -> bytes:
    """Compile a sample module and put it in ``store``; return its wasm bytes."""
    wasm = compile_wat(name)
    store.put(wasm)
    return wasm
