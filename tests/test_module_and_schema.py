"""Module resolution + schema faults (reviewer findings 2, 3)."""

from __future__ import annotations

import hashlib
import json
import os

from helix_gate import testkit as tk
from helix_gate.errors import ReasonCode
from conftest import submit


# --- module resolution (finding 3) ----------------------------------------

def test_module_not_found_rejected(harness):
    wasm = tk.compile_wat("answer")  # NOT published to the store
    env = harness.issuer.envelope(wasm=wasm)
    assert submit(harness, env).reason is ReasonCode.REJECTED_MODULE_NOT_FOUND


def test_module_digest_mismatch_rejected(harness):
    """Validly signed for digest D, but the stored bytes at D are tampered."""
    good = tk.publish(harness.store, "answer")
    digest = hashlib.sha256(good).hexdigest()
    env = harness.issuer.envelope(wasm=good)
    with open(os.path.join(harness.store._root, digest + ".wasm"), "wb") as fh:
        fh.write(good + b"\x00")  # corrupt the artifact after signing
    assert submit(harness, env).reason is ReasonCode.REJECTED_MODULE_DIGEST_MISMATCH


def test_unsupported_interface_rejected(harness):
    wasm = tk.publish(harness.store, "answer")
    env = harness.issuer.envelope(wasm=wasm, interface_version="helix:execution@9.9.9")
    assert submit(harness, env).reason is ReasonCode.REJECTED_MODULE_INTERFACE_MISMATCH


# --- schema (finding 2) ----------------------------------------------------

def test_malformed_json_rejected(harness):
    assert harness.gate.submit("{not json", owner="x").reason is ReasonCode.REJECTED_MALFORMED


def test_unknown_schema_version_rejected(harness):
    wasm = tk.publish(harness.store, "answer")
    env = harness.issuer.envelope(wasm=wasm)
    env["schema_version"] = "HX9/9.9"
    assert submit(harness, env).reason is ReasonCode.REJECTED_SCHEMA_VERSION_UNSUPPORTED


def test_missing_field_rejected(harness):
    wasm = tk.publish(harness.store, "answer")
    env = harness.issuer.envelope(wasm=wasm)
    del env["nonce"]
    assert submit(harness, env).reason is ReasonCode.REJECTED_SCHEMA_INVALID


def test_unknown_top_field_rejected(harness):
    wasm = tk.publish(harness.store, "answer")
    env = harness.issuer.envelope(wasm=wasm)
    env["surprise"] = "extra"
    assert submit(harness, env).reason is ReasonCode.REJECTED_SCHEMA_INVALID


def test_negative_resource_limit_rejected(harness):
    wasm = tk.publish(harness.store, "answer")
    env = harness.issuer.envelope(wasm=wasm, limits=tk.default_limits(fuel=-1))
    assert submit(harness, env).reason is ReasonCode.REJECTED_SCHEMA_INVALID
