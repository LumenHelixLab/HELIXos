# HELIXos Security Model — Actor, Credential, and Privilege Specification

**Status:** binding. Companion to `../SPEC.md` and `docs/glossary.md`. This document is the Milestone-0 deliverable "actor/role model + privilege matrix" from `../../HELIXos_Handoff_Audit.md` §7 item 6. Terms capitalized here are defined in `docs/glossary.md`.

## 1. Purpose, scope, and audit traceability

This document fixes the audit findings that v2.0 left unresolved in the security plane. Scope: all M0 code, with normative forward requirements for M2 (channel governance) and M3 (possession lease automation) stated where the audit's roadmap places them.

| Audit finding | Defect | Fixed in |
|---|---|---|
| AUD-C2 | `!POSSESS` accepted any `owner_token`; the only access control was a comment | §3.1, §5 (constant-time hash auth, rate limit, audit) |
| AUD-C3 | `manifest()` dispatched arbitrary unaudited commands, bypassing governance; CRLF smuggling | §4, §5.2, §6 T5 |
| AUD-C6 | Symmetric HMAC: every verifier is also a forger; no key management | §3.2–3.4 (per-actor Ed25519, rotation) |
| AUD-M3 | Krishna role circularity: possessed agent, "root owner", and God process conflated | §2.2 |
| AUD-H5 | Governance over plaintext IRC, tokens on the wire, flat trust misnamed "DMZ" | §2.3, §5.3, §7 |
| AUD-M8 (partial) | `+mode` treated as authorization; no channel registry or ACLs | §7 |

## 2. Actor and role model

### 2.1 Actors

| Actor | Kind | Identity established by | Home (per SPEC §1) | Summary of authority |
|---|---|---|---|---|
| **Human Owner** | human principal — the sole root authority | Knowledge of `HELIXOS_OWNER_TOKEN`; holder of the owner Ed25519 root key (M2+) | outside the system; acts through the KRISHNA vessel and (M2) the owner IRC console | May `!POSSESS`, `manifest` (through possessed KRISHNA), trigger Projective Collapse, rotate all credentials |
| **KRISHNA** | agent — God, Manifestor vessel | Own Ed25519 keypair; `agent_id == "KRISHNA"` enforced by `KrishnaManifestor` | `aigent-os-kernel/orchestrator/possession.py` | Generates/verifies instructions, dispatches via BABEL; while possessed, executes the owner's `manifest()` commands; cannot possess itself |
| **KALI** | agent — God, Ledger-keeper | Own Ed25519 keypair | M3 (`orchestrator/`, `HELIXvault/robot-agents/`) | Generates/verifies instructions, dispatches via BABEL; watches EventJournal hash-chain integrity; renders no vault files |
| **NATASHA** | agent — Spider | Own Ed25519 keypair | M3 (`HELIXvault/robot-agents/`) | Generates/verifies instructions, dispatches via BABEL; subscribes `#t-gateway`; no governance authority |
| **CHARLOTTE** | agent — Spider | Own Ed25519 keypair | M3 (`HELIXvault/robot-agents/`) | Same as NATASHA |
| **BABEL dispatcher** | in-process mechanism — **not a principal** | none (holds no credentials) | `aigent-os-kernel/src/BABEL/dispatcher.py` | Routes validated commands; audits every dispatch; its authority is exactly the sum of its callers' validated commands |
| **vault-bridge** | service — sole vault writer | Service identity (M3); no bus authority | `Obsidian-Brain/` views (M3) | Reads journal/bus, writes rendered vault views; cannot dispatch, possess, or sign bus instructions |
| **knotcore sidecar** | data service — **not a principal** | none; must never see signing keys or the owner token | `aigent-os-kernel/KNOTstore_bin/sidecar/` | Executes `store_instruction` / `fetch_payload` / `verify_cauldron_phase` mechanically under the caller's authority |

Two rules follow from the table: (1) mechanisms (BABEL, sidecar) never appear as grantees of authority — they execute under a caller's authority and attribute audit records to that caller; (2) every human action is mediated: the owner acts *through* the possessed KRISHNA vessel or (M2) through signed application-level commands, never by writing system files directly.

### 2.2 Resolution of the Krishna circularity (AUD-M3)

v2.0 made Krishna simultaneously a possessed agent (L76), the "root owner" who triggers `!POSSESS` (L89), a Quaternity member (L13), and a LangGraph "Gods" process (L102), which is circular: if Krishna is root owner, "possessing Krishna" possesses the possessor. The canonical resolution:

1. **The Human Owner is the sole root authority.** Every privileged capability in the system derives from the owner and is revocable by the owner.
2. **KRISHNA-the-agent is a process**, specifically the *designated Manifestor vessel* — the only agent the possession protocol may bind to. This is enforced in code: `KrishnaManifestor.__init__` raises `ValueError` for any `agent_id` other than `"KRISHNA"` (SPEC §3.6).
3. **Possession binds a verified owner token to the KRISHNA agent instance for a lease period.** While the lease holds, the owner's commands flow through `manifest()` into BABEL under the current fencing token, fully audited. An unpossessed KRISHNA is an ordinary God agent with no manifest capability.
4. v2.0 L89 ("Only the root owner (Krishna) can trigger !POSSESS") is corrected to: **only the Human Owner, acting through the KRISHNA vessel, can possess.** The owner token belongs to the human, never to the agent; the agent holds only its SHA-256 hash.
5. KALI's ledger duties are exercised through the EventJournal (append, integrity-watch), not by "two Gods managing one markdown file" — `Council_Ledgers.md` is a vault-bridge-rendered view (glossary §4.4).

### 2.3 Trust boundaries (DMZ posture)

The bus is a DMZ in posture (glossary §3.6): transport identities are untrusted, all lines are forgeable, relays log plaintext. Inside the boundary: agent processes, the knotcore sidecar, and the journal host filesystem. Outside: everything arriving from the bus. Crossings are permitted only as (a) verified triangulated instructions, (b) validated BABEL commands, or (c) possession requests with a valid owner token. Secrets never cross the bus. Until M2 delivers TLS + SASL (§7), no security decision may rely on nick, host, or channel mode.

## 3. Credential model

### 3.1 Credential inventory

| Credential | Type | Held by | Stored as | Source | Mode |
|---|---|---|---|---|---|
| Owner token | high-entropy bearer secret | Human Owner only | **SHA-256 hash only**, via `KrishnaManifestor.hash_token` | env `HELIXOS_OWNER_TOKEN` | all modes |
| Ed25519 private seed | 32-byte raw seed | each Quaternity agent (own key); owner root key (M2+) | private seed, file-permission/env protected | env `HELIXOS_ED25519_SK` (hex) | production |
| Ed25519 public keys | verifier keyring (one entry per actor, with `kid`, `not-before`, `not-after`) | all verifiers | non-secret config | `configs/helixos.yaml` | production |
| HMAC dev key | symmetric, ≥ 32 bytes | all M0 dev/test processes (shared) | secret, env only | env `HELIXOS_HMAC_KEY` (hex) | **dev only** |

Standing rules: secrets come only from env (SPEC §4); no secret is ever logged, rendered into a vault view, or transmitted on the bus; the owner token exists in cleartext only in the owner's hands and in the process environment at bootstrap — inside the process it is immediately reduced to its SHA-256 hash.

The dev HMAC key is symmetric, so every holder can forge as any actor (audit AUD-C6). This is an accepted **test-only** posture: dev HMAC mode MUST NOT be deployed outside tests, and CI MUST NOT set `HELIXOS_HMAC_KEY` for any artifact that ships.

### 3.2 Per-mode signing (SPEC §3.1)

- **Dev (HMAC):** 16-byte truncated HMAC-SHA-256 over the `HELIX-BUS/2` canonical encoding, rendered as 22 base64url chars; verification by recompute + `hmac.compare_digest`.
- **Production (Ed25519):** full 64-byte signature rendered as 86 base64url chars in the tag field (truncated Ed25519 signatures are unverifiable; see SPEC §3.1 NOTE and ADR-004). Generation requires the actor's private seed; verification requires only that actor's public key from the keyring.

Per-actor keys are the AUD-C6 fix: a compromised or prompt-injected agent can still misuse *its own* key (bounded by the verb allowlist and the privilege matrix), but it cannot forge *another* actor's lines, and revocation is a keyring edit, not a coordinated global rekey.

### 3.3 Key rotation procedure

**R1 — Ed25519 agent key rotation (planned):**
1. Generate the new keypair; assign a new `kid`.
2. Add the new public key to the keyring in `configs/helixos.yaml` with `not-before = now`; deploy to all verifiers.
3. Switch the rotating agent's signer to the new seed.
4. Drain: keep the old public key in the keyring with `not-after = now + MAX_CLOCK_SKEW_S (300 s) + in-flight margin` so pre-rotation lines still verify.
5. Remove the old public key after `not-after`.
6. Journal the rotation: `{"type":"security.key_rotation","payload":{"actor":..., "kid_new":..., "kid_old":..., "epoch":...}}`.

**R2 — HMAC dev key rotation (unplanned/emergency):** generate a fresh ≥ 32-byte key; update `HELIXOS_HMAC_KEY` for every dev process in one deploy; restart; bump the cauldron generation of every outstanding slot to invalidate tags minted under the old key. Global rekey is the accepted dev-mode cost of a symmetric key.

**R3 — Owner token rotation:** the owner generates a new high-entropy token; the new SHA-256 hash replaces the stored hash (authenticated by the current valid token, or by out-of-band console access if the current token is lost); the fencing token increments; the event is journaled (`security.owner_token_rotation`). The old token is useless immediately.

**R4 — Suspected compromise:** treat as Tampered-class. Rotate per R1/R3, trigger a Projective Collapse (epoch increment) to fence all in-flight material, and review the journal back to the last clean `verify_chain()` pass.

Tags do not carry a `kid` in M0: exactly one signing key per actor is active at any time, and rotation history lives in the keyring and the journal.

## 4. Privilege matrix

Deny-by-default: any capability not marked **A** (allow) is denied. **M** marks a mechanism that executes the operation under a caller's authority without holding authority itself. Every **A** is still constrained by input validation (verb allowlist, 400-byte cap, control-character rejection) and is audited.

| Capability | Human Owner | KRISHNA | KALI | NATASHA | CHARLOTTE | BABEL | vault-bridge | knotcore sidecar |
|---|---|---|---|---|---|---|---|---|
| Generate instruction | A (owner root key, or via possessed KRISHNA) | A | A | A | A | D | D | D — stores payloads but cannot sign (note 2) |
| Verify instruction | A | A | A | A | A | A | A | M — phase predicate only (note 3) |
| Dispatch via BABEL | via `manifest()` only — D directly | A | A | A | A | M — is the mechanism | D | D |
| `!POSSESS` | **A — only the owner** | D (is the vessel; cannot self-possess) | D | D | D | D | D | D |
| `manifest` | A — through possessed KRISHNA | A — only while possessed under a valid lease | D | D | D | D | D | D |
| Write journal | D (owner actions are journaled by KRISHNA) | A | A | A | A | A — audit events attributed to caller | D | D |
| Write vault | D (inbox files only) | D | D | D | D | D | **A — sole writer** | D |
| Bump cauldron generation | D | A | A | A | A | D | D | M — executes op for an allowed caller |
| Trigger Projective Collapse | A | A | A | A | A | D | D | D |

Notes:
1. **Generate** requires a signing key. In production each agent signs as itself (§3.2); in dev HMAC mode all four agents share one key — a documented test-only risk (§3.1).
2. The sidecar's `store_instruction` is a data operation, not instruction generation: it persists an envelope but produces no tag. Only a caller holding a signing key can mint a bus line.
3. The sidecar's `verify_cauldron_phase` is the store-side phase predicate (one component of verification), invoked under the caller's authority; the fail-closed policy decision stays in `KNOT_API_WRAPPER.verify_instruction_lock`.
4. **Verify** for vault-bridge is read-only: it verifies to render views accurately and gains no dispatch authority.
5. **Bump cauldron generation** marks an instruction's lifecycle progress (invalidating older tags' phase checks). Only the four agents may request it, and only for slots in their own instruction lifecycle; the sidecar executes mechanically.
6. BABEL and the sidecar hold no credentials and appear as grantees of nothing; their audit writes are mechanism events attributed to the calling actor.

## 5. Possession protocol

Normative protocol for owner override, implemented per the staging in §5.5. All steps are fail-closed: any internal error leaves the system RELEASED.

### 5.1 State machine

```
            P1 authenticate (ok) + P2 fence
RELEASED ──────────────────────────────────► POSSESSED
   ▲        {fencing_token = f, lease_expiry}   │
   │                                            │ P4 explicit release (authenticated)
   │                                            │ OR lease expiry
   └────────────────────────────────────────────┘
```

- `RELEASED` — `possessed_by_owner == False`; `manifest()` raises `PossessionDenied`.
- `POSSESSED{f, lease_expiry}` — `manifest()` accepts commands under fencing token `f` until `lease_expiry`.
- Process restart always lands in RELEASED (no persistence of possession; fail-closed by construction).

### 5.2 Protocol steps

**P1 — Authenticate.** Compute `candidate = SHA-256(owner_token)` and compare against the stored hash with `hmac.compare_digest` (constant time). On mismatch: append the failure to the rate-limit window, audit `possession.denied agent=%s`, raise `PossessionDenied("invalid owner token")`. The raw token is never stored or logged.

**P2 — Fence.** Under the manifestor's `threading.Lock`, increment `self.fencing_token` and begin the new lease. The fencing token totally orders leases: any `manifest()` associated with a stale fencing value (e.g. a replayed grant or a holder from before a re-possession) is invalid. The fencing token is included in the audit record (SPEC §3.6 ADD).

**P3 — Lease.** Possession is granted with expiry `LEASE_TTL_S = 900` seconds from grant. The lease is **not** auto-renewed by activity; continued possession requires re-authentication with the token. On expiry the manifestor transitions to RELEASED and audits the expiry with the fencing token. While possessed, every `manifest()` call is validated against the manifest command grammar (glossary §3.2: non-empty, ≤ 400 bytes UTF-8, no `\r`/`\n`/`\x00`, first token a registered verb), audited as `manifest.dispatch agent=%s cmd=%r`, and executed via the injected dispatcher.

**P4 — Release.** Either an explicit authenticated release (the toggle path with a valid token) or lease expiry clears `possessed_by_owner` under the lock and audits `possession.toggled agent=%s state=%s fencing=%d`. Release is idempotent in effect: a system already RELEASED stays RELEASED.

### 5.3 Rate limiting and audit

- **Rate limit:** at most `RATE_LIMIT = 5` failed authentications per `RATE_WINDOW = 300` s sliding window (monotonic clock) per manifestor instance. While at the limit, authentication attempts raise `PossessionDenied("rate limited")` and audit `possession.rate_limited agent=%s` — before any token comparison work is reported, so the limiter cannot be used as an oracle amplifier. Counters are per-process; bus-level (cross-connection) limiting arrives with the M2 gateway (§7, audit AUD-M7 token buckets).
- **Audit events (all with fencing token where a lease exists):** `possession.denied`, `possession.rate_limited`, `possession.toggled` (state + fencing), `manifest.refused` (reason), `manifest.dispatch` (command repr). Token values NEVER appear in any record.

### 5.4 Parameters

| Parameter | Value | Defined in |
|---|---|---|
| `RATE_LIMIT` | 5 failures | `possession.py` (SPEC §3.6) |
| `RATE_WINDOW` | 300 s | `possession.py` (SPEC §3.6) |
| `LEASE_TTL_S` | 900 s | this document §5.2 (automation staged to M3, §5.5) |
| Max command length | 400 bytes UTF-8 | `possession.py`, `dispatcher.py` (SPEC §3.5–3.6) |
| Forbidden characters | `\r`, `\n`, `\x00` | `possession.py`, `dispatcher.py` (SPEC §3.5–3.6) |
| Allowed vessel | `agent_id == "KRISHNA"` | `possession.py` (SPEC §3.6) |
| Token at rest | SHA-256 digest (32 bytes) | `possession.py` (`hash_token`, SPEC §3.6) |

### 5.5 M0 implementation status (staging, normative)

Per SPEC §3.6 (adopting audit §6.2 with fencing), M0 implements: P1 (constant-time hash compare), P2 (fencing increment on every possess transition, in the audit record), P4 (explicit toggle release), rate limiting (§5.3), KRISHNA-only construction, full command validation, and audit of every transition. Automatic lease-expiry enforcement (the P3 timer) ships with the M3 orchestrator per audit §7 (M3 exit criterion: "possession with authenticated token, fencing, lease expiry, full audit trail"). Until then, possession is operator-session-bound and release is explicit: deployments MUST NOT leave a possessed KRISHNA unattended, and runbooks (`infra/runbooks/`) MUST include possession release in shutdown procedures.

## 6. Threat model

Six abuse cases, each mapped to the control that defeats it. "Enforced in" cites the repo file(s) per SPEC §1.

### T1 — Compromised or prompt-injected agent (fixes AUD-C6, AUD-M3)

**Attack:** an agent process is compromised or its model is prompt-injected; the attacker mints forged instructions or submits rogue commands.
**Controls:** per-actor Ed25519 keys — a compromised verifier holds only public keys and cannot forge other actors' lines (production mode, §3.2); the verb allowlist (`READ/WRITE/EXEC/ARCHIVE`) bounds the blast radius of any minted line; BABEL validation rejects unregistered verbs with `UnknownCommand`; the privilege matrix (§4) denies agents vault writes, `!POSSESS`, and self-possession; every dispatch and verification is journaled for after-the-fact attribution; revocation is a keyring edit (rotation R1), not a global rekey.
**Enforced in:** `aigent-os-kernel/KNOTstore_bin/signers.py`, `aigent-os-kernel/KNOTstore_bin/KNOT_API_WRAPPER.py`, `aigent-os-kernel/src/BABEL/dispatcher.py`, `aigent-os-kernel/orchestrator/possession.py`, `configs/helixos.yaml`.
**Residual risk:** dev HMAC mode is symmetric — confined to tests by §3.1.

### T2 — Replayed instruction (fixes AUD-C5, AUD-H3)

**Attack:** an observed bus line is re-injected — after completion, after revocation, or after a collapse.
**Controls:** every stored payload is a freshness envelope `{"ts":int,"nonce":b64url(12B),"body":...}`; the verifier enforces a 300 s clock-skew window (`MAX_CLOCK_SKEW_S`) and a seen-nonce cache, so a byte-identical resubmission fails; the cauldron phase check takes a caller-supplied expected phase, so completed/advanced slots reject old lines; epoch fencing (glossary §1.4) rejects anything stamped before the current epoch after a collapse.
**Enforced in:** `aigent-os-kernel/KNOTstore_bin/KNOT_API_WRAPPER.py`, `aigent-os-kernel/KNOTstore_bin/knotcore_sim.py`, `aigent-os-kernel/src/memory/epochs.py`.

### T3 — Verb-swap / pointer-rebind forgery (fixes AUD-C4, AUD-M1)

**Attack:** flip `READ` → `EXEC`, or re-point a valid tag at a different slot, keeping the tag.
**Controls:** the braid signature is computed over the canonical `b"HELIX-BUS/2"` length-prefixed encoding of ptr ‖ verb ‖ stored-envelope — changing any axis invalidates the tag; comparison is constant-time (`hmac.compare_digest` in dev mode, the Ed25519 library verifier in production); the strict `BUS_RE` charsets (hex ptr, `[A-Z0-9_]{1,16}` verb, base64url tag) plus the verb allowlist reject delimiter injection and bidi/Trojan-Source verbs at parse time.
**Enforced in:** `aigent-os-kernel/KNOTstore_bin/KNOT_API_WRAPPER.py`, `aigent-os-kernel/KNOTstore_bin/signers.py`, wire grammar in `SPEC.md` §2.

### T4 — Owner-token brute force (fixes AUD-C2, AUD-H5)

**Attack:** online guessing of the `!POSSESS` token.
**Controls:** the token is stored only as a SHA-256 hash; comparison is constant-time; 5 failures per 300 s trigger denial + audit; the token never traverses the bus (M2: private message only, §7) and is never logged; the token carries no derivable structure (high-entropy bearer secret from env).
**Enforced in:** `aigent-os-kernel/orchestrator/possession.py`.

### T5 — IRC line smuggling (fixes AUD-C3)

**Attack:** a manifest command or payload containing `\r\n` injects raw IRC lines (`MODE`, `PRIVMSG`) into the daemon, executing protocol actions as the agent.
**Controls:** `\r`, `\n`, and `\x00` are rejected at BOTH the possession boundary and the dispatcher boundary (defense in depth); commands are capped at 400 bytes; on the bus itself every field is charset-constrained (hex ptr, `[A-Z0-9_]` verb, base64url tag) so a syntactically valid line cannot contain protocol metacharacters.
**Enforced in:** `aigent-os-kernel/orchestrator/possession.py` (`_validate_command`), `aigent-os-kernel/src/BABEL/dispatcher.py` (validation), `SPEC.md` §2 (`BUS_RE`).

### T6 — Store-flood DoS (fixes AUD-H2, completes AUD-C8)

**Attack:** flood `store_instruction` to exhaust slots, wrapping pointers onto victim payloads or forcing a collapse per verification failure — a cheap total denial of service.
**Controls:** write-once 64-bit slots with bounded capacity (default `2**16`) — exhaustion raises `StoreFull`, a dev-visible error, and never wraps or aliases; the collapse taxonomy excludes availability failures (`Unavailable` → circuit-break/retry only), so exhaustion-induced verification failures cannot trigger collapse storms; the sidecar imposes 2 s per-call deadlines and a circuit breaker (5 consecutive errors → open, 30 s reset) bounding caller impact; from M2, per-sender token buckets on `#t-gateway` throttle the ingress itself.
**Enforced in:** `aigent-os-kernel/KNOTstore_bin/knotcore_sim.py` (`StoreFull`, `configure(capacity)`, `bump_generation`), `aigent-os-kernel/KNOTstore_bin/sidecar/sidecar_client.py` (deadline + breaker), `aigent-os-kernel/src/memory/epochs.py` (collapse taxonomy), §7 (M2 rate limits).

## 7. Channel governance (M2 normative requirements)

Forward requirements for the Layer-B bus per audit §7 (M2 exit criteria) and findings AUD-H5/M8. Nothing here relaxes an M0 control; the wire format and application-level verification remain authoritative regardless of transport security.

### 7.1 Transport security

- TLS is mandatory for every client connection; the production daemon exposes no plaintext listener.
- SASL authentication is mandatory (PLAIN over TLS, or EXTERNAL with client certs); the four Quaternity nicks and the owner-console nick are registered and reserved.
- Until both are in place, the DMZ posture (§2.3) applies: nick, host, and channel mode are never inputs to a security decision.

### 7.2 Channel taxonomy and ACL sketch

Every channel is registered in the M2 channel registry (name, class, TTL, ACL) and belongs to exactly one class (glossary §5.1). BABEL consults the registry before acting on any bus-originated command.

| Channel | Class | Speak | Read | Notes |
|---|---|---|---|---|
| `#t-gateway` | Thought | Quaternity (voice, post-SASL) | Quaternity + owner console | Request ingress; per-sender token bucket; no deliberation |
| `#t-ops`, `#t-<task>` | Thought (TTL'd per-task) | Task members (registry-assigned) | Task members + owner console | Created by registry entry, auto-expire at TTL |
| `#think-*` | Thinking | All agents + owner console | All agents + owner console | Deliberation; non-authoritative by construction; never dispatched |
| `#audit` | append-only audit | Services only (write) | Owner console | Mirrors possession, manifest, collapse, and rotation events |

`+mode` is applied as defense in depth after SASL (e.g. `+m` on `#t-gateway`, voice for the four agents) and is never authoritative (glossary §3.5).

### 7.3 Application-level signed commands

Every privileged operation over the bus — `!POSSESS`, channel-registry changes, mode changes on governance channels, Projective-Collapse epoch announcements — MUST be an application-level signed command: the canonical command text signed with the owner's Ed25519 root key, verified before execution against the keyring, and journaled with the fencing token (possession) or epoch (collapse). Channel-operator status is a display hint only; an unsigned or unverifiable privileged command is dropped regardless of the sender's mode.

### 7.4 Audit channel

`#audit` is append-only: services hold write, the owner console holds read, no agent holds write. It mirrors, at minimum: `possession.*`, `manifest.*`, `babel.dispatch` for privileged verbs, `security.key_rotation`, `security.owner_token_rotation`, and every Projective Collapse with old/new epoch and cause. The EventJournal remains the system of record; `#audit` is its human-facing mirror.

## 8. Normative security invariants (summary)

1. Root authority is the Human Owner; agents hold delegated, revocable, per-actor credentials.
2. Secrets come from env only; the owner token is stored as a SHA-256 hash; no secret is logged or sent on the bus.
3. Verification is fail-closed everywhere (`verify_instruction_lock` never raises); generation raises on invalid input.
4. Every authenticator binds ptr + verb + stored envelope; freshness is enforced (300 s skew + seen-nonce cache); staleness is fenced by cauldron phase and epoch.
5. Possession is authenticate → fence → lease → release; 5 failures / 300 s rate limit; every transition audited; KRISHNA is the only vessel.
6. vault-bridge is the sole vault writer; the EventJournal is the sole system of record; caches are disposable.
7. The sidecar is the only process touching the store; every call is deadline-bounded behind a circuit breaker; `Unavailable` never triggers a collapse.
8. Nick, host, and `+mode` never authorize; every privileged operation carries an application-level credential.
9. `\r`/`\n`/`\x00` rejection and the 400-byte cap hold at every command boundary.
10. Dev HMAC mode never leaves test.
