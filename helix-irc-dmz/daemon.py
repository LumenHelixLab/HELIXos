"""Headless IRC daemon — Layer 2 transport (spec §1, Layer 2).

Hosts the Publish/Subscribe DMZ over which all agents communicate. Provides the
``#T-*`` (persistent Thought) and ``#t-*`` (ephemeral thinking) channel classes
and enforces the mode-granting policy (``+o``/``+v``) fed by ``helix_hub``.

An existing daemon (InspIRCd or similar) may back this; this module defines the
HELIXos-specific policy surface. Not implemented yet.
"""

from __future__ import annotations


def serve(host: str = "127.0.0.1", port: int = 6667) -> None:
    """Start the headless IRC DMZ. Blocks until shutdown."""
    raise NotImplementedError(
        "IRC DMZ daemon not implemented. Decide first whether to embed "
        "InspIRCd or ship a custom server (see helix-irc-dmz/README.md)."
    )
