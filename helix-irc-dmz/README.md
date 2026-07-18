# helix-irc-dmz — Layer 2 (The Transmission Layer / Nervous System)

A locally hosted, headless IRC daemon acting as a Publish/Subscribe message
broker and Agent DMZ. See [`docs/SPECIFICATION.md` §1, Layer 2](../docs/SPECIFICATION.md#layer-2--the-transmission-layer-helix-irc--dmz).

## Modules

| File | Responsibility |
|---|---|
| `daemon.py` | Lightweight headless IRC server. |
| `helix_hub.py` | Heartbeat and Pub/Sub router over the IRC channels. |

## Channel topology

- `#T-*` — **Thought** channels: persistent strategy zones mapped to `genome.html`.
- `#t-*` — **thinking** channels: ephemeral, high-speed worker execution zones.

Agents earn IRC modes (`+o` op, `+v` voice) as a function of their
prediction-error-minimization efficiency (spec §1, "Dynamic Power").

## Notes

An off-the-shelf daemon (e.g. InspIRCd) satisfies the transport contract; a
custom daemon is only warranted if the mode-granting policy must live inside the
server rather than in `helix_hub.py`.
