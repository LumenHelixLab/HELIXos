"""Helix Hub — heartbeat and Pub/Sub router (spec §1, Layer 2).

Sits on top of the IRC ``daemon`` and:

* emits the system heartbeat that other layers phase-lock to;
* routes published messages to subscribers by channel class (``#T-*`` / ``#t-*``);
* computes the prediction-error efficiency scores that ``daemon`` turns into
  ``+o``/``+v`` mode grants.

Not implemented yet; depends on ``daemon.serve`` being available.
"""

from __future__ import annotations


class HelixHub:
    """Pub/Sub router and heartbeat source for the DMZ."""

    def __init__(self) -> None:
        raise NotImplementedError(
            "Depends on helix-irc-dmz/daemon.py transport."
        )
