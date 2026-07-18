# 🧬 HELIXos

**Multi-Agent Interconnect & Predictive Active Inference Substrate.**

HELIXos is a four-tier system that isolates cognitive processing (LLM agents)
from physical execution (a reversible state-machine kernel) and long-term memory
(a topological archive), connected by a headless IRC "nervous system" and a
linguistic translation layer.

The full architecture, agent taxonomy, linguistic protocols, and operational
mechanics live in **[`docs/SPECIFICATION.md`](docs/SPECIFICATION.md)** (Master
Engineering Handoff v3.0).

## Layers

| Layer | Package | Role |
|---|---|---|
| 1 — Cognitive Engine (TEN²) | [`aigent-os-kernel/`](aigent-os-kernel/) | Reversible 10-state finite machine; the system "physics." |
| 2 — Transmission (Helix IRC / DMZ) | [`helix-irc-dmz/`](helix-irc-dmz/) | Headless IRC Pub/Sub broker and Agent DMZ. |
| 3 — Archive (AKASH / KNOTstore) | [`aigent-os-kernel/knot_api_wrapper.py`](aigent-os-kernel/knot_api_wrapper.py) | Zero-trust topological long-term memory. |
| 4 — Distributor (Babel Tower / Fish) | [`babel-tower/`](babel-tower/) | Linguistic clutch, dispatcher, and human interface. |
| UI & Memory State | [`obsidian-brain/`](obsidian-brain/) | Genome DNA, HUD telemetry, ledgers, Ronin cold storage. |
| Agents | [`agents/`](agents/) | LLM wrappers for the Quaternity and X-Men tiers. |

## Status

⚠️ **Specification + scaffold stage.** This repository contains the v3.0
specification and a directory scaffold mirroring it. Every Python module is a
documented stub that raises `NotImplementedError` and records its intended
contract and open questions — no kernel math, error-correction, cryptographic,
or LLM behavior has been implemented or verified. See each package's `README.md`
for build order and blockers, and the
[Implementation Status](docs/SPECIFICATION.md#implementation-status) section of
the spec.

## License

[CC0 1.0 Universal](LICENSE) (public domain dedication).
