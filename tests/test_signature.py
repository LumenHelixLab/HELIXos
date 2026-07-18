"""Signature / key faults (reviewer finding 1)."""

from __future__ import annotations

import base64
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from helix_gate import testkit as tk
from helix_gate.errors import GateOutcome, ReasonCode
from conftest import submit


def test_tampered_payload_rejected(harness):
    wasm = tk.publish(harness.store, "answer")
    env = harness.issuer.envelope(wasm=wasm)
    env["target"] = "#t-evil"  # mutate a signed field after signing
    res = submit(harness, env)
    assert res.outcome is GateOutcome.REJECTED
    assert res.reason is ReasonCode.REJECTED_SIGNATURE_INVALID


def test_algorithm_confusion_rejected(harness):
    wasm = tk.publish(harness.store, "answer")
    env = harness.issuer.envelope(wasm=wasm)
    env["signature"]["alg"] = "rsa"
    assert submit(harness, env).reason is ReasonCode.REJECTED_ALG_UNSUPPORTED


def test_unknown_key_rejected(harness):
    wasm = tk.publish(harness.store, "answer")
    env = harness.issuer.envelope(wasm=wasm, key_id="key-does-not-exist")
    assert submit(harness, env).reason is ReasonCode.REJECTED_KEY_UNKNOWN


def test_revoked_key_rejected(harness):
    wasm = tk.publish(harness.store, "answer")
    harness.keyring.revoke(harness.issuer.key_id)
    env = harness.issuer.envelope(wasm=wasm)
    assert submit(harness, env).reason is ReasonCode.REJECTED_KEY_REVOKED


def test_foreign_signer_rejected(harness):
    """A validly-signed envelope from a key not in the ring is rejected."""
    wasm = tk.publish(harness.store, "answer")
    stranger = tk.Issuer(name=harness.issuer.name)  # same issuer name, unknown key
    env = stranger.envelope(wasm=wasm)
    assert submit(harness, env).reason is ReasonCode.REJECTED_KEY_UNKNOWN


def test_garbage_signature_bytes_rejected(harness):
    wasm = tk.publish(harness.store, "answer")
    env = harness.issuer.envelope(wasm=wasm)
    env["signature"]["bytes"] = base64.b64encode(b"not-a-real-signature").decode()
    assert submit(harness, env).reason is ReasonCode.REJECTED_SIGNATURE_INVALID
