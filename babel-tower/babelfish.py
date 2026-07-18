"""Babel Fish — the Owner's interface (spec §1 Layer 4, §4.1).

A Textual TUI plus Obsidian MCP proxy middleware. Intercepts high-speed machine
traffic and renders it as semantic, human-readable strings; runs the Rosetta
Loop (colloquial input -> strict CLI syntax via a local LLM, surfaced through an
IntelliSense drop-down for user approval).

Not implemented yet; depends on ``babel_dispatcher`` for the machine-side feed.
"""

from __future__ import annotations


def run() -> None:
    """Launch the Babel Fish TUI."""
    raise NotImplementedError(
        "Babel Fish TUI pending: needs babel_dispatcher parsing + a local LLM "
        "for the Rosetta Loop."
    )
