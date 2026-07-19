# ADR-003: Adopt Ergo as the M2 IRC Daemon (Build-vs-Buy)

| | |
|---|---|
| **Status** | Accepted |
| **Date** | M0 hardening pass |
| **Audit finding(s) resolved** | **AUD-H7** (no acceptance criteria; M2 committed to a from-scratch IRCd with zero build-vs-buy analysis); findings-register items **F2.4, F7.1** (SPOF daemon; missing acceptance/exit gates) |
| **Governs** | M2 "Helix IRC Nervous System" deliverable; bus transport behind the Triangulated Bus (SPEC §2); HA and scalability envelope |

---

## 1. Context

The v2.0 handoff committed Milestone 2 to *implementing* "the Helix IRC Daemon
(HelixIRCd)… a headless IRC message broker" from scratch, in Python, with **zero
build-vs-buy analysis** (AUD-H7). The audit's assessment was blunt: a from-scratch
Python IRCd would be **the slowest, least-tested component in the system and its single
point of failure** — while mature daemons (Ergo, InspIRCd) already provide TLS, SASL,
services (NickServ/ChanServ), and history for free.

The bus is load-bearing: every agent-to-agent message, every `!POSSESS` governance
command, and every audit record flows over it (SPEC §2 wire format). AUD-M7 added the
scalability reality: a Python IRCd realistically sustains ~10³ msgs/s with O(members)
fan-out, and the 512-byte IRC line cap forces non-trivial payloads through KNOTstore
indirection. AUD-H5 requires TLS + SASL on the governance plane — work a from-scratch
daemon would have to build and get right on the first try.

AUD-H7's meta-finding stands behind all of this: **no acceptance criteria or exit
gates existed for any milestone.** This ADR is itself one of the M0 deliverables
(audit §7 item 5): a written build-vs-buy rationale.

## 2. Decision

### 2.1 Buy: Ergo

**HELIXos adopts [Ergo](https://ergo.chat/) (formerly Oragono) as the M2 IRC daemon.**
The from-scratch "HelixIRCd" deliverable is cancelled as a daemon implementation
project; the name survives only as the *deployment configuration* of Ergo.

Rationale:

| Property | Ergo gives us | From-scratch Python IRCd would require |
|---|---|---|
| **TLS** | Built-in, battle-tested | Build + audit a TLS termination path (AUD-H5 blocker) |
| **SASL** | Built-in; identity bound to accounts, not nicks | Build SASL + an account system from zero (AUD-H5 blocker) |
| **Services** | NickServ/ChanServ-equivalent account & channel registration | Build identity/registration services (AUD-M8 blocker) |
| **History / chathistory** | Server-side playback for rejoining agents | Build persistence + replay ourselves |
| **Maturity** | Years of production use, active maintenance, protocol conformance testing | The least-tested component we own, carrying the busiest path |
| **Operations** | Single static Go binary, YAML config, tiny footprint | A new Python service with its own packaging, supervision, and perf unknowns |

The audit's core argument is adopted verbatim: an IRCd is **solved infrastructure**;
HELIXos's novelty lives above the daemon, not inside it.

### 2.2 What remains custom (the actual M2 work)

The build-vs-buy decision removes the daemon, not the milestone. HELIXos still builds:

| Component | Why it is ours |
|---|---|
| **BABEL dispatch protocol** (`dispatcher.py`, SPEC §3.5) | The command grammar and validation/audit discipline over the bus is core HELIXos IP |
| **Channel registry** | Naming conventions, TTL'd per-task channels, discovery directory consulted by BABEL (AUD-M8) |
| **Thought/Thinking partitioning ACLs** | Role-based speak/listen policy, expressed over Ergo accounts + channel modes + application-level signed commands; the *policy* is ours even though enforcement primitives are Ergo's |
| **`vault-bridge` bot** | Sole Obsidian writer; subscribes to the bus, renders journal-derived markdown (ADR-001 §2.2) |
| **Governance commands** | `!POSSESS` etc. as application-level signed commands (ADR-004), not raw +mode |

### 2.3 High-availability story

A single daemon is still a single point of failure; buying Ergo changes its *quality*,
not its singularity. The HA story for M2:

1. **Supervisor auto-restart:** Ergo runs under the same systemd supervision substrate
   as the sidecar (ADR-002 §2.2); a crash restarts in seconds.
2. **Reconnect-with-jitter:** every agent client implements exponential backoff with
   random jitter on disconnect, so a daemon bounce does not produce a reconnect
   thundering herd.
3. **Journal resume:** agents resume from the journal (ADR-001) after reconnect —
   replaying events missed during the gap — so a daemon outage degrades delivery but
   loses no state. The journal, not the daemon, is the record.
4. **Degradation mode documented:** with the daemon down, agents continue local work;
   governance commands queue; the runbook in `infra/runbooks/` covers the drill.

**SPOF mitigation path, in order of escalation:**

| Stage | Trigger | Action |
|---|---|---|
| 1 | Daemon crash (seconds) | Supervisor restart + client reconnect-with-jitter + journal resume (default, no action) |
| 2 | Host loss or repeated crashes | Second Ergo instance, **linked daemons** (Ergo clustering), agents fail over by config |
| 3 | Sustained scale beyond envelope (§2.4) | Data plane moves to NATS (or Redis Streams); IRC remains the human-ops control plane |

### 2.4 Honest scalability envelope

Published as a bound, not a hope:

> **The IRC data plane is sized for ~dozens of agents and ~10³ msgs/s aggregate.**
> Past either boundary, fan-out cost, flood-control disconnects (AUD-M7), and
> per-connection buffers make IRC the wrong tool, and the data plane moves to a real
> broker (NATS / Redis Streams). IRC is retained permanently as the **human-legible
> control plane** — that property, not throughput, is why IRC was chosen.

At M1–M3 scale (4 agents, LLM steps of 100 ms–10 s), the system runs at roughly
1–10² msgs/s: two orders of magnitude below the envelope ceiling. This is tracked, not
assumed: per-agent message rates are metered (AUD-M7's "quantify msg rates now"), and
approaching 50% of the envelope trips the Stage-3 evaluation above.

## 3. Consequences

### 3.1 Positive

- **AUD-H5's blockers arrive for free**: TLS and SASL are configuration, not code;
  identity binds to accounts, making the governance channel's ACLs meaningful.
- **The riskiest self-build disappears**: no newborn Python daemon on the busiest path;
  the SPOF that remains is at least a *mature* SPOF with a defined escalation path
  (§2.3).
- **M2 scope shrinks to differentiated work** (§2.2): protocol, registry, ACLs, bridge
  — the pieces that are actually HELIXos.
- **Rejoining agents get history** (chathistory) and, via ADR-001, authoritative state
  resume from the journal: two independent recovery layers.
- **Single Go binary** simplifies packaging, CI integration tests
  (docs/latency-budgets.md §4.3 run against it), and the M3 chaos drill
  (kill the daemon → journal resume verified).
- **Exit gates now exist**: this ADR plus the acceptance criteria in the audit §7
  roadmap close the "no definitions of done" half of AUD-H7 for the M2 transport.

### 3.2 Negative

- **Operational dependency**: Ergo is Go, outside the project's Python/stdlib skill
  base; deep misconfigurations require reading another project's docs. Mitigated by
  keeping the config small and checked into `configs/`.
- **Not all semantics are ours**: IRC protocol quirks (512-byte lines, flood limits,
  mode arcana) leak into HELIXos; the KNOTstore indirection for oversized payloads
  (AUD-L2/M7) remains mandatory.
- **Clustering is Stage 2, not day one**: until linked daemons are deployed, the
  single instance is a real SPOF; the HA story mitigates but does not eliminate it.
- **Feature negotiation**: Thought/Thinking ACLs must be expressed within what Ergo
  accounts/modes plus application-level signing can enforce; exotic channel semantics
  that don't map get cut, not patched into a fork.
- **Ceiling is real**: teams that ignore §2.4 will rediscover it under load. The
  metering requirement is the guardrail.

## 4. Alternatives considered

| Alternative | Why rejected |
|---|---|
| **From-scratch Python "HelixIRCd"** (v2.0 plan) | Slowest, least-tested component and the SPOF; must rebuild TLS/SASL/services/history; rejected per AUD-H7/M7 |
| **InspIRCd** | Capable and mature, but C++ oper-oriented daemon with heavier config and module system; account/SASL model depends on external services (Atheme). Ergo's integrated accounts + single binary fit a 4-agent research system better |
| **ngIRCd / miniircd** | Lighter, but minimal services/history; AUD-H5 and rejoin-resume requirements push the work back onto us |
| **NATS / Redis Streams as the day-one bus** | Right tool past the envelope (§2.4), wrong tool for M2: loses the human-legible "any IRC client is a HITL console" property the audit called genuinely clever, and adds a broker before scale justifies it |
| **Matrix / XMPP control plane** | Vastly heavier protocol surface for zero gain at this scale; human-legibility goal is better served by IRC's simplicity |

## 5. Audit finding(s) resolved

- **AUD-H7** — the build-vs-buy analysis now exists in writing with a defensible
  conclusion (§2.1, §4); M2 exit gates are defined (HA drill, ACL registry, resume
  verification).
- **F2.4** (findings register; SPOF daemon) — §2.3 defines supervision, reconnect,
  resume, and the staged SPOF-elimination path.
- **F7.1** (findings register; acceptance criteria) — §2.2–2.4 give M2 testable
  deliverables and a quantified envelope replacing the untestable "capable of
  handling… partitioning" claim.
