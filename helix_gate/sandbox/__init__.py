"""Disposable, capability-restricted Wasm execution sandbox (Layer 1 boundary)."""

from __future__ import annotations

from .controller import ExecReport, ExecSpec, SandboxController

__all__ = ["SandboxController", "ExecSpec", "ExecReport"]
