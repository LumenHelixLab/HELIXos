# HELIXos

**HELIXos** is an agentic operating system: a small, verifiable kernel over
which a fixed set of four agents (the Quaternity — NATASHA & CHARLOTTE the
Spiders, KALI & KRISHNA the Gods) communicate over a signed, grammar-checked
Triangulated Bus, with an append-only hash-chained journal as the single
system of record and an Obsidian vault as the human cognitive UI. State-changing
facts are journaled before they take effect; everything else — ledgers, the
vault, zero-copy memory — is a regenerable view or a disposable cache.

This repository is the **v2.0 → M0 specification-hardening pass** of HELIXos,
executed against the findings of `../HELIXos_Handoff_Audit.md`. The v2.0
handoff shipped a poetic but unbuildable design (48-bit HMAC tags, 256-slot
aliasing pointers, an unowned IRCd, undefined "Projective Collapse", no
source of truth, no engineering substrate). M0 pins every load-bearing term
(`SPEC.md` is binding), fixes the security model (ADR-004: 64-bit write-once
pointers, dual-mode HMAC-dev/Ed25519-prod signing, freshness envelopes),
moves the proprietary knotcore out-of-process behind a circuit-broken sidecar
(ADR-002), defines collapse as an epoch-fenced replay protocol (ADR-001),
chooses Ergo as the M2 bus daemon (ADR-003), and lands the platform
scaffolding — CI gates, config, systemd units, and runbooks (AUD-H8) — that
every later milestone builds on.

## Milestones

| Milestone | Theme | Status |
|---|---|---|
| M0 | Specification hardening: corrected wrapper/possession, sidecar, journal, FSM, docs, platform | ✅ Done (139 tests) |
| M1 | Core foundation: AKASH braid signatures, FSM executor, snapshots, chaos-tested recovery ([SPEC-M1.md](SPEC-M1.md), [acceptance](docs/m1-acceptance.md)) | ✅ Done (290 tests total) |
| M2 | Bus layer: Ergo IRCd, partitions (ADR-003) | ⬜ Planned |
| M3 | Agents + human cognitive UI: Quaternity, LangGraph orchestration, lease timers, Obsidian vault | ⬜ Planned |

## M1 — Core Foundation (current)

M1 lands the runtime core on top of the hardened M0 bus (SPEC-M1.md):

- **`aigent-os-kernel/src/akash/braid.py`** — verifiable **AKASH braid
  signatures**: one hash-chained strand per agent, woven by crossings;
  `sign_root`/`verify_root_signature` (domain-separated, HMAC-dev /
  Ed25519-prod) and `anchor_braid`/`verify_anchor` — the braid root anchored
  on the bus as an ordinary `ARCHIVE` instruction.
- **`aigent-os-kernel/src/runtime/executor.py`** — `FSMExecutor`: bus line →
  fail-closed verify → TEN-SQUARED transition → journaled → braid-committed;
  `instruction.rejected` taxonomy (invalid/stale/replay/phase),
  `backend.unavailable` chaos contract, `collapse()` (Projective Collapse)
  and `recover()` from snapshot + journal.
- **`aigent-os-kernel/src/memory/snapshot.py`** — atomic, hash-pinned,
  optionally Ed25519-signed checkpoints (seq, epoch, FSM state, braid root,
  strand tips) with fail-closed load and chain-verified journal replay.
- **Chaos-tested recovery** (`tests/test_m1_chaos.py`, 6 tests): 10k-instruction
  soak, sidecar SIGKILL mid-stream + resume, phase-bump forward security,
  randomized recovery equivalence, tampered-journal and tampered-braid
  evidence. Acceptance report: `docs/m1-acceptance.md`.

```bash
python3 -m pytest            # 290 tests (M0 139 + braid 73 + executor/snapshot 72 + M1 acceptance 6)
python3 scripts/demo_m1.py   # "M1 SMOKE: Core Foundation" — 51 checks, exit 0
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Layer D — Human cognitive UI (M3)                                       │
│   Obsidian-Brain/  ← rendered ONLY by vault-bridge (ADR-001 §2.2)       │
│   humans write to Obsidian-Brain/inbox/ only                            │
├─────────────────────────────────────────────────────────────────────────┤
│ Layer C — Agents (M3)                                                   │
│   Quaternity: NATASHA CHARLOTTE (Spiders) · KALI KRISHNA (Gods)         │
│   HELIXvault/: babel-lang/ robot-agents/ prompts/                       │
│   owner override: KrishnaManifestor (KRISHNA-only, fenced, rate-limited)│
├─────────────────────────────────────────────────────────────────────────┤
│ Layer B — Bus (M2)                                                      │
│   Ergo IRCd (ADR-003) · Thought #t-* / Thinking #think-* partitions     │
│   wire: [ {ptr16hex} | {VERB} | #{tag22|86} ]  (SPEC §2)                │
├─────────────────────────────────────────────────────────────────────────┤
│ Layer A — Kernel (M0, this pass)                                        │
│   ten_squared_fsm (100-state, p99<1ms) · BABEL dispatcher               │
│   EventJournal (system of record) + EpochFence (split-brain fencing)    │
│   KNOT_API_WRAPPER ──► SimBackend | SidecarBackend                      │
│                        knotcore_sim · sidecar (Unix sock, breaker, ABI) │
│   signers: HMACSigner (dev) · Ed25519Signer (prod)  — keys from env only│
└─────────────────────────────────────────────────────────────────────────┘
```

## Repository layout (SPEC §1)

```
HELIXos/
├── SPEC.md                    # binding M0 spec (single source of truth)
├── SPEC-M1.md                 # binding M1 addendum (braid, executor, snapshots)
├── conftest.py                # flat import namespace: seven code dirs on sys.path
├── pyproject.toml             # metadata + pytest/ruff/mypy config
├── aigent-os-kernel/
│   ├── src/runtime/{ten_squared_fsm,executor}.py  # 100-state FSM + FSM executor
│   ├── src/memory/{journal,epochs,snapshot}.py    # system of record + fencing + checkpoints
│   ├── src/BABEL/dispatcher.py               # command grammar + audit
│   ├── src/akash/braid.py                    # AKASH braid signatures (M1)
│   ├── KNOTstore_bin/{signers,knotcore_sim,KNOT_API_WRAPPER}.py
│   ├── KNOTstore_bin/sidecar/{rpc_protocol,sidecar_server,sidecar_client}.py
│   └── orchestrator/possession.py            # KrishnaManifestor
├── tests/                     # pytest suite (+ vectors/golden_vectors.json, test_m1_chaos.py)
├── configs/helixos.yaml       # documented node config (secrets: env only)
├── configs/helixos.schema.json# JSON Schema 2020-12 for the config
├── docs/                      # glossary, latency budgets, ADR-001..004
├── infra/systemd/             # sidecar, ircd (Ergo), vault-bridge units
├── infra/runbooks/            # sidecar-outage, projective-collapse, key-rotation
├── scripts/demo_m0.py         # end-to-end M0 demo
├── scripts/demo_m1.py         # end-to-end M1 demo ("M1 SMOKE: Core Foundation")
├── HELIXvault/                # agent toolbelt homes (M2/M3; skeleton)
├── Obsidian-Brain/            # human cognitive UI (skeleton; inbox/ live)
└── .github/workflows/ci.yml   # lint / type-check / test / latency-gate
```

## Quickstart

Requires Python ≥ 3.11 (3.12 recommended). From the repo root:

```bash
# 1. Dependencies — stdlib-only by design; pytest for the suite,
#    cryptography for Ed25519 production signing (ADR-004)
pip install pytest cryptography

# 2. Run the full test suite (conftest.py wires the import namespace)
python3 -m pytest

# 3. Run the knotcore sidecar (Unix-socket RPC wrapper, ADR-002)
export HELIXOS_SIDECAR_SOCKET=/tmp/helixos-knotcore.sock
python3 aigent-os-kernel/KNOTstore_bin/sidecar/sidecar_server.py &

# 4. Set dev secrets (env only — NEVER in config files; SPEC §3.10)
export HELIXOS_HMAC_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
export HELIXOS_OWNER_TOKEN="dev-owner-token"

# 5. End-to-end demo
python3 scripts/demo_m0.py    # NOTE: lands in Stage 2 (integrator-owned)
```

`scripts/demo_m0.py` (Stage 2) exercises the M0 modules exactly per SPEC §3 —
the flow it runs, against the same APIs you can use today:

```python
import os
from signers import HMACSigner, HMACVerifier                 # §3.1 (dev mode)
from KNOT_API_WRAPPER import (                               # §3.3
    generate_triangulated_instruction, verify_instruction_lock)
from dispatcher import BabelDispatcher                       # §3.5
from possession import KrishnaManifestor                     # §3.6
from journal import EventJournal                             # §3.7

key = bytes.fromhex(os.environ["HELIXOS_HMAC_KEY"])           # ≥32 bytes
signer, verifier = HMACSigner(key), HMACVerifier(key)

# generate + verify a triangulated bus instruction (fail-closed verify)
line = generate_triangulated_instruction("hello helix", "WRITE", signer)
assert verify_instruction_lock(line, verifier) is True

# owner possession → manifest a BABEL command (KRISHNA-only, fenced, audited)
dispatcher = BabelDispatcher()
dispatcher.register("PING", lambda args: "PONG")
owner_hash = KrishnaManifestor.hash_token(os.environ["HELIXOS_OWNER_TOKEN"].encode())
manifestor = KrishnaManifestor("KRISHNA", owner_hash, dispatcher)
manifestor.toggle_possession(os.environ["HELIXOS_OWNER_TOKEN"])

# journal the fact (append-only, fsync, hash-chained)
journal = EventJournal(os.environ.get("HELIXOS_JOURNAL_PATH", "./helixos-journal.jsonl"))
journal.append("demo.instruction", {"line": line})
assert journal.verify_chain()
```

Config: `configs/helixos.yaml` (validated by `configs/helixos.schema.json`).
Env vars (SPEC §3.10): `HELIXOS_HMAC_KEY`, `HELIXOS_ED25519_SK`,
`HELIXOS_OWNER_TOKEN`, `HELIXOS_JOURNAL_PATH`, `HELIXOS_SIDECAR_SOCKET`,
`HELIXOS_LOG_LEVEL`.

## M0 exit criteria (audit §7) → resolving files

| # | Audit §7 M0 item | Resolving file(s) |
|---|---|---|
| 1 | Glossary of load-bearing terms | `docs/glossary.md` |
| 2 | Per-layer latency budgets | `docs/latency-budgets.md`; enforced by `.github/workflows/ci.yml` (latency-gate, FSM p99 < 1000 µs) |
| 3 | Source-of-truth decision | `docs/adr/ADR-001-journal-source-of-truth.md`; `aigent-os-kernel/src/memory/journal.py`, `epochs.py` |
| 4 | knotcore sidecar spike | `docs/adr/ADR-002-knotcore-sidecar.md`; `aigent-os-kernel/KNOTstore_bin/{signers,knotcore_sim,KNOT_API_WRAPPER}.py`, `KNOTstore_bin/sidecar/{rpc_protocol,sidecar_server,sidecar_client}.py`; `tests/vectors/golden_vectors.json` |
| 5 | IRCd build-vs-buy decision | `docs/adr/ADR-003-ircd-build-vs-buy.md`; `infra/systemd/helix-ircd.service` |
| 6 | Actor/role model + privilege matrix | `docs/security-model.md`; `aigent-os-kernel/orchestrator/possession.py` (fencing, KRISHNA-only); `configs/helixos.yaml` `channel_registry` ACLs |
| 7 | Platform scaffolding (AUD-H8) | `conftest.py`, `pyproject.toml`, `.github/workflows/ci.yml`, `configs/helixos.yaml` + `helixos.schema.json`, `infra/systemd/*.service`, `infra/runbooks/`, this README, `HELIXvault/` + `Obsidian-Brain/` skeletons |
