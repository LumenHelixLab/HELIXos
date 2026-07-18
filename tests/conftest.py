"""Shared pytest fixtures for the Gate 2 suite."""

from __future__ import annotations

import json

import pytest

from helix_gate import testkit as tk


@pytest.fixture
def harness(tmp_path):
    """A fresh gate + issuer + module store on a per-test temp dir."""
    return tk.build_harness(str(tmp_path))


@pytest.fixture
def permissive_harness(tmp_path):
    """A harness whose policy allows large resource limits (for run-long tests)."""
    policy = tk.default_policy(limit_ceilings={
        "fuel": 10 ** 13, "wall_ms": 120_000, "memory_bytes": 16 * 65536,
        "table_elems": 1024, "stack_bytes": 1_048_576, "output_bytes": 4096,
        "host_calls": 0,
    })
    return tk.build_harness(str(tmp_path), policy=policy)


def submit(h, env, owner="tester"):
    """Submit an envelope dict and return the GateResult."""
    return h.gate.submit(json.dumps(env), owner=owner)
