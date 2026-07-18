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
| 1 — Execution boundary (**Gate 2**) | [`helix_gate/`](helix_gate/) | **Implemented + tested.** Hardened Wasm adapter: HX1 signature/replay/policy validation, process-isolated execution with resource limits, interruptible cancellation, durable registry, signed audit log. See [`docs/GATES.md`](docs/GATES.md). |
| 2 — Transmission (Helix IRC / DMZ) | [`helix-irc-dmz/`](helix-irc-dmz/) | Headless IRC Pub/Sub broker and Agent DMZ. |
| 3 — Archive (AKASH / KNOTstore) | [`aigent-os-kernel/knot_api_wrapper.py`](aigent-os-kernel/knot_api_wrapper.py) | Zero-trust topological long-term memory. |
| 4 — Distributor (Babel Tower / Fish) | [`babel-tower/`](babel-tower/) | Linguistic clutch, dispatcher, and human interface. |
| UI & Memory State | [`obsidian-brain/`](obsidian-brain/) | Genome DNA, HUD telemetry, ledgers, Ronin cold storage. |
| Agents | [`agents/`](agents/) | LLM wrappers for the Quaternity and X-Men tiers. |

## Status

**Gate 2 is implemented and tested; the rest is specification + scaffold.**

- ✅ **`helix_gate/` (Gate 2)** — the hardened Wasm execution boundary is real
  code with 48 conformance + fault-injection tests. Quick start:
  ```
  pip install -r requirements.txt
  pytest -q                      # 48 tests
  python -m helix_gate.demo      # live end-to-end + fault-injection tour
  ```
- ⚠️ **Everything else** — the kernel/IRC/babel/agent trees are documented stubs
  that raise `NotImplementedError` and record their intended contract; no kernel
  math, error-correction, or LLM behavior there is implemented yet.

See each package's `README.md` for build order and blockers, the
[Implementation Status](docs/SPECIFICATION.md#implementation-status) section of
the spec, and [`docs/GATES.md`](docs/GATES.md) for the security-gate model.

## License

[CC0 1.0 Universal](LICENSE) (public domain dedication).
