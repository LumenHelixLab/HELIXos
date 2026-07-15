# Core Dev Planning Team — Analysis & Design Notes

**Status: Specified (proposals).** This document analyses [Core Development Multi-Agent Planning Team v2.0](../specs/core-dev-planning-team.md) and proposes resolutions for gaps found in it. Nothing here is implemented; each proposal needs Council approval before it amends the v2.0 spec.

---

## 1. What the spec gets right

* **Separation of planning from execution.** The team's only outputs are `todolist.md` and Canvas state. This keeps the planning layer honest under the [Live-Operation Directive](../specs/live-operation-directive.md): a plan is not execution, and the planning team never claims otherwise.
* **Tamper-evidence at the instruction level.** Binding pointer + verb + hash at creation means the unit of trust is the atomic task, not the whole document. A corrupted todolist fails loudly and locally.
* **HITL is structural, not advisory.** Variance failures physically halt the pipeline (red node + Markdown alert) rather than logging a warning that scrolls away.
* **Model auditing gates the whole plan**, not individual instructions — correct, since one unaudited planner can poison scopes that other agents then depend on.

## 2. Defects and underspecified areas

### 2.1 The 1-byte pointer space is too small (critical)

A `TinyPointer` of 1 byte gives **256 addresses**. A real project plan easily exceeds 256 atomic tasks, and every **Rewrite** consumes a fresh pointer (immutable-pointer invariant, spec §9), so churn burns address space even faster.

**Proposal:** scope the pointer namespace rather than widening the wire format. A pointer is unique within `(project_id, domain, plan_epoch)`:

* 256 addresses per domain per planning cycle (4 domains → 1,024 per epoch);
* the fully-qualified address is `project/epoch/domain/0xNN`; only the 1-byte tail travels on the Triangulated Instruction Bus, preserving the compact format;
* `KNOT_API_WRAPPER` must expose `remaining_address_space(domain)` and the Overseer must halt decomposition (red node, HITL) when a domain drops below a configurable floor (default 16), rather than failing mid-triangulation.

If Council prefers a flat namespace instead, the pointer must grow to 2 bytes and the bus format version-bumps — that is a breaking change to every consumer.

### 2.2 `variance_threshold_percent` default of 85% is inverted (critical)

Spec §5: in `proxy-ceo` mode the Overseer auto-overrides "if the overall variance (percentage of failed instructions) is below the configured `variance_threshold_percent` (default 85%)."

As written, a plan where **84% of instructions fail cryptographic verification is auto-approved without a human**. That contradicts the spec's own invariant (§9, "HITL Always Enforced for Anomalies") in spirit, and it treats an integrity failure like a quality metric.

**Proposal:** split failures into two classes with different policies:

| Failure class | Examples | proxy-ceo behaviour |
| --- | --- | --- |
| **Integrity failure** | hash mismatch, unknown pointer, secret mismatch, Cauldron phase failure | **Never auto-overridden.** Always red node + manual HITL, regardless of threshold. These indicate tampering or hallucination, and no percentage of them is acceptable. |
| **Soft variance** | verb outside the agent's allowed list, scope drift into another domain, payload missing optional fields | Threshold-based auto-override permitted. Sane default: **5%**, not 85%. |

The 85% figure survives only if reinterpreted as a *pass-rate floor* ("proceed if ≥ 85% verified") — if that was the intent, the spec text must be rewritten to say so, and integrity failures still must be excluded from any auto-override.

### 2.3 The 8-hex-digit hash is display-grade, not proof-grade

`#F7A2B1C3` is 32 bits. That is fine as a human-readable digest but trivially collidable if an adversary (or a hallucinating model) can choose payloads. Tamper-*evidence* against accidental corruption: yes. Tamper-*proofing*: no.

**Proposal:** the authoritative binding is a full **HMAC-SHA-256** over `(fully_qualified_pointer ‖ verb_codepoint ‖ payload)` keyed with the per-agent secret, stored inside KNOTstore alongside the payload. The 8-hex tag on the bus is the truncated display digest. `verify_instruction_lock` verifies the **full MAC**, never just the display tag. The spec's claim "any change to the payload after generation will invalidate the hash" then becomes true in the cryptographic sense.

### 2.4 "No overlap allowed" has no arbitration rule

Real requirements are cross-cutting: "auth" touches frontend forms, backend middleware, API contracts, and a users table. The spec forbids overlap but never says how the Overseer splits a cross-cutting requirement.

**Proposal — ownership and interface contracts:**

* `api-plan` **owns every cross-domain boundary**: any data shape or endpoint that two domains share is an API-contract instruction, and the other domains reference it, never restate it.
* The Overseer's decomposition emits, per cross-cutting requirement, one *contract instruction* (api-plan) plus *consumer instructions* (other domains) that carry a `depends_on` reference to the contract's pointer.
* A dedicated soft-variance check: if two instructions in different domains resolve to payloads targeting the same file/artifact, the Variance Gateway flags both yellow ("scope collision") for the Overseer to re-split.

### 2.5 The todolist has no ordering or dependency model

`todolist.md` is a flat checklist, but the downstream execution layer needs to know that the migration runs before the middleware that queries the new column. Without ordering, "ready-to-execute" is an overclaim.

**Proposal:** use what the stack already has — **Canvas edges are the dependency DAG**:

* every Triangulated Instruction is a Canvas node; every `depends_on` is a directed edge in `project_state.canvas`;
* the Markdown todolist gains an optional trailing annotation: `[ 0x51 | ⛁ | #A1B2C3D4 ] Integrate auth middleware ← 0x22, 0x3C`;
* the Variance Gateway adds a **cycle check** — a dependency cycle is a soft variance (yellow), because the plan cannot be executed as ordered;
* the Overseer's PHASE 4 merge emits domains in topological order so a naive top-to-bottom executor is still correct.

### 2.6 Rewrite loops are unbounded

§5 lets the human choose **Rewrite Instruction**, sending it back to the Skilled Agent — with no limit. A planner that keeps hallucinating pointers loops forever with a human clicking Rewrite.

**Proposal:** `max_rewrite_attempts` per instruction (default **3**). On exhaustion the instruction is marked red-permanent, the options collapse to **[Override & Proceed] | [Abort Plan] | [Reassign Domain]**, and the failure (model, prompt ref, attempt payload hashes) is appended to the audit ledger so the model registry review sees repeat offenders.

### 2.7 Green conflates two different truths

§6 defines Green as "verified, ready for execution (**or already executed** by downstream layer)." Under the Live-Operation Directive those are distinct claims — the second requires real, independently verified effects; the first is plan-level only.

**Proposal:** Green means **plan-verified only**. Execution status is a separate node property written back by the execution layer, mapped to the Directive's status labels (§8): `Connected`, `Write-Validated`, `Workflow-Validated`. The HUD renders execution status as a node **border/badge**, never by repainting the plan-status fill colour. The planning team never writes execution status — that would be fabricating operational results.

### 2.8 The payload schema must equal the Directive's execution package

§4 says the KNOTstore payload holds "the exact requirement, file specs, logic." The Live-Operation Directive (§4) requires more before any operator may act: expected result, verification method, timeout and loop limits, rollback instructions, permitted accounts/files/domains, prohibited actions, approval state, audit reference.

**Proposal:** the KNOTstore payload **is** the execution package. Planning-time fields the planner can't know (approval state) are stamped by the Overseer at PHASE 4. A payload missing any mandatory package field is a soft variance — the instruction is grey-blocked, not merged. This makes the planning→execution handoff lossless and keeps the "ready-to-execute" claim honest.

### 2.9 Missing verbs

The registry has refactor/scaffold/integrate/optimize/migrate/version — but the spec's own validation section requires test tasks, and no planner has a verb for *create/test/document/configure/remove*. Planners will either misuse ⬢ for everything or be unable to plan required work.

**Proposal:** extend the canonical verb registry (single source of truth, listed with explicit codepoints since several of these glyphs render ambiguously across fonts):

| Verb | Codepoint | Meaning | Domains |
| --- | --- | --- | --- |
| ⬢ | U+2B22 | refactor | all |
| ⨮ | U+2A2E | scaffold | frontend |
| ⛁ | U+26C1 | integrate | backend, db |
| ⛟ | U+26DF | optimize | backend |
| ⛃ | U+26C3 | migrate | db |
| ⛮ | U+26EE | version | api |
| ⊕ | U+2295 | create *(new)* | all |
| ⊗ | U+2297 | remove *(new)* | all, always soft-flagged yellow for HITL review |
| ⊨ | U+22A8 | test *(new)* | all |
| ✎ | U+270E | document *(new)* | all |
| ⚙ | U+2699 | configure *(new)* | backend, db, api |

Verb-out-of-domain remains a soft variance caught by the Gateway (verbs are advisory routing, the hash is the security boundary).

### 2.10 Concurrency and ledger drift

Nothing stops the Council from amending `Council_Ledger.md` mid-planning, leaving the plan verified against requirements that no longer exist.

**Proposal:** at PHASE 1 the Overseer records `ledger_hash = SHA-256(Council_Ledger.md)` into the plan header and every instruction's audit reference. The Variance Gateway re-reads the ledger at PHASE 3; a hash mismatch is a **plan-level red** ("ledger drift") requiring restart-or-override. One planning epoch is single-flight per project: a second decomposition request while an epoch is open is rejected.

### 2.11 Secrets and key management are hand-waved

`generate_triangulated_instruction(payload, unicode_verb, secret)` — whose secret? If all five agents share one key, any planner can forge instructions attributed to another, defeating the no-cross-agent-leak invariant.

**Proposal:** per-agent derived keys: `k_agent = HKDF(master, project_id ‖ agent_id ‖ epoch)`, issued at team instantiation, held in-memory only, rotated per epoch. `verify_instruction_lock` runs under the Overseer's verify capability, which can check any agent's MAC but **cannot generate** one (KNOTstore enforces the generate/verify split internally — consistent with the Overseer's "never reveals plain-text secrets" constraint and the KNOTstore-blackbox rule). Forged attribution then fails verification structurally, not by policy.

## 3. Glossary debts

The spec uses these terms without definition; each needs a stub before the spec is self-contained:

| Term | Working definition (to be confirmed) |
| --- | --- |
| **Corpus Callosum** | The message-passing layer between the Pipeline Council and functional teams; delivers `Council_Ledger.md`. |
| **Cauldron phase-duality verification** | KNOTstore's internal integrity check invoked via `verify_cauldron_phase(phase=0)`; blackbox per §9 — only the boolean result is observable. |
| **Numo memory** | The Overseer's frozen-state store used during quarantine; must survive a session restart so a red plan resumes at the same HITL prompt. |
| **HELIXvault** | Read-only prompt/asset store referenced by `prompt_ref`; contents are versioned and hashed. |
| **Chairperson** | The authorized human whose "Approved" unblocks `manual` mode; identity/auth mechanism TBD (Directive §6 requires unambiguous identity before approval is accepted). |

## 4. Live-Operation Directive compliance map

Because the planning team is itself software, it is subject to the Directive:

| Directive rule | Application to this team |
| --- | --- |
| No mock content (§1) | Validation tests (spec §10) must call the real `KNOT_API_WRAPPER` against a real KNOTstore instance; fixture ledgers are permitted only inside the test suite and labeled as fixtures. A demo that shows green nodes must have actually run the Variance Gateway. |
| Real integrations (§3) | `obsidian-mcp` and the KNOTstore wrapper each need a health check (read + authorized write) before the team may report itself operational; otherwise they are **Not Implemented**. |
| Status labels (§8) | The team's own components carry Directive labels. Current state of everything in this document: **Specified**. |
| Verify before record (§5) | Canvas writes count as ACT; the Overseer must re-read `project_state.canvas` (VERIFY) after writing node colours before recording the HITL event — a failed MCP write must not produce a green node in the ledger. |
| Human handoff (§6) | The HITL alert format in spec §5 must carry the Directive's required fields: current state, requested action, why, consequence, safe options, resume point. The current three-button alert covers "safe options" only; the template needs the other five fields. |

## 5. Proposed acceptance additions (beyond spec §10)

5. **Pointer exhaustion:** decompose a ledger yielding > 240 frontend tasks; confirm the Overseer halts with a red node *before* triangulation rather than failing mid-run.
6. **Integrity vs. soft variance:** corrupt one hash *and* one verb in the same plan under `proxy-ceo`; confirm the hash failure forces manual HITL while the verb failure alone would have auto-overridden.
7. **Dependency cycle:** create two instructions depending on each other; confirm the Gateway yellows both and the merge is blocked.
8. **Ledger drift:** modify `Council_Ledger.md` between PHASE 1 and PHASE 3; confirm plan-level red with reason "ledger drift".
9. **Rewrite exhaustion:** force 3 failed rewrites of one instruction; confirm the option set collapses and the audit ledger records all attempts.
10. **Key isolation:** attempt to verify an instruction generated with agent A's key while claiming agent B's identity; confirm structural failure.

## 6. Open questions for the Council

1. Pointer namespace: scoped 1-byte (proposal §2.1) or flat 2-byte with a bus version bump?
2. Is 85% a mis-stated pass-rate floor or a wrong default? (§2.2 — needs an explicit answer recorded in the spec.)
3. Should `api-plan` owning all cross-domain contracts be promoted from convention to a Gateway-enforced rule?
4. Does the execution layer write execution status back into `project_state.canvas` directly, or through the Overseer? (Direct writes are simpler; Overseer-mediated writes keep a single Canvas writer.)
5. Who is the Chairperson's identity provider, and does "Approved" require anything stronger than a typed string in `manual` mode?
