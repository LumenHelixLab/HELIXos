# HELIXos Core Development Multi-Agent Planning Team

**Specification v2.0 — OG HELIXos Features Only (No Robot Execution)**

## 1. Overview

The Core Development multi-agent planning team is the prefrontal cortex of HELIXos. It decomposes a Council-approved project ledger into fully verified, triangulated task lists, enforces cryptographic integrity via KNOTstore, and manages the Human-In-The-Loop (HITL) through the Tactical HUD. It does not execute code. Its output is a ready-to-execute `todolist.md` and an updated Obsidian Canvas — the actual code execution is handled by a separate, stateless execution layer outside the scope of this planning team.

The team consists of:

* **1 Overseer (Lead Architect)** – gatekeeper, strategic decomposer, variance enforcer.
* **4 Skilled Agents (Planners)** – each specialises in a domain (Frontend, Backend, Database, API) and produces a sequence of Triangulated Instructions.

All communication is through the Triangulated Instruction Bus and the Obsidian-mounted ledgers.

## 2. Team Roles and Responsibilities

### 2.1 Overseer – Lead Architect (Core Dev)

| Property     | Value                                                                                                          |
| ------------ | -------------------------------------------------------------------------------------------------------------- |
| Role         | Gatekeeper, strategic decomposer, HITL enforcer.                                                                |
| Capabilities | Decompose Council Ledger, assign non-overlapping scopes, verify instruction locks, trigger HITL, update Canvas nodes. |
| Outputs      | Aggregated `todolist.md` with Triangulated Instructions, real-time HUD node colour changes (red/yellow/green).  |
| Constraints  | Never writes code, never accesses KNOTstore internals, never reveals plain-text secrets.                        |

### 2.2 Skilled Agents

Each Skilled Agent is a specialised LLM instance (e.g., Codex) that operates only during the planning phase. Their output is a sequence of Triangulated Instructions — not source code.

| Agent ID        | Scope of Decomposition                                | Allowed Unicode Verbs               |
| --------------- | ----------------------------------------------------- | ----------------------------------- |
| `frontend-plan` | UI component tree, state management, client-side logic | ⬢ (refactor), ⨮ (scaffold)          |
| `backend-plan`  | Server routes, business logic, authentication          | ⬢, ⛁ (integrate), ⛟ (optimize)      |
| `db-plan`       | Schema design, migrations, indexing strategies         | ⛃ (migrate), ⛁                      |
| `api-plan`      | Endpoint contracts, DTOs, versioning                   | ⛮ (version), ⬢                      |

**Mandatory Rule:** Skilled Agents output only Triangulated Instructions. No plain-text secrets, no full code blocks, no direct file paths outside the pointer abstraction.

## 3. The OG HELIXos Feature Stack

This planning team is built entirely on the core HELIXos primitives:

* **Obsidian Cognitive UI** – all ledgers, task lists, and the Tactical HUD live as Markdown/Canvas files in `Obsidian-Brain/`.
* **KNOTstore** – proprietary, O(1) address-regenerating storage; all task payloads are stored here, referenced by opaque 1-byte pointers.
* **Triangulated Instruction Bus** – 3-axis symbolic command `[ TinyPointer | Unicode | #HexHash ]` ensures efficiency, tamper-evidence, and IP protection.
* **Variance Gateway** – validates every instruction against KNOTstore's Cauldron phase-duality verification before the plan is approved.
* **Tactical HUD (Canvas)** – real-time visual feedback of task status, with colour-coded nodes for anomalies, overrides, or completions.
* **Pipeline Council Integration** – receives `Council_Ledger.md` from the Corpus Callosum and returns a verified plan.

## 4. Task Lifecycle (Planning Only)

The lifecycle is purely planning-focused; execution is out of scope.

```text
[ Council_Ledger.md ]  ← received from Pipeline Council
        │
        ▼
[ PHASE 1 – Decomposition ]   Overseer splits into 4 non-overlapping scopes
        │
        ├──► frontend-plan   (UI)
        ├──► backend-plan    (server)
        ├──► db-plan         (data)
        └──► api-plan        (contracts)
        │
        ▼
[ PHASE 2 – Triangulation ]   Each agent produces a todolist_{domain}.md with Triangulated Instructions
        │
        ▼
[ PHASE 3 – Variance Gateway & HITL ]   All instructions verified; if fail → red node & halt
        │ (if pass or human override)
        ▼
[ PHASE 4 – Plan Approval ]   Overseer merges todolists into final todolist.md and updates Canvas to green
```

### Decomposition Rules

1. Overseer parses the `Council_Ledger.md` and extracts the Core Dev requirements.
2. Responsibilities are split strictly along the four domains; no overlap allowed.
3. Each Skilled Agent receives a private `domain_context.md` (in-memory) containing only its assigned requirements.

### Triangulation Protocol

Each Skilled Agent calls `KNOT_API_WRAPPER.generate_triangulated_instruction(payload, unicode_verb, secret)` for every atomic task. The Overseer collects them into a unified `todolist.md`:

```markdown
# Core Dev Execution Plan
## Frontend
- [ ] [ 0x3A | ⨮ | #F7A2B1C3 ]  Scaffold React component tree
- [ ] [ 0x7F | ⬢ | #19C4D88E ]  Refactor state management
## Backend
- [ ] [ 0x51 | ⛁ | #A1B2C3D4 ]  Integrate auth middleware
...
```

The actual payload (the exact requirement, file specs, logic) lives inside KNOTstore and is referenced only by the pointer. The hex hash provides tamper-evidence; any change to the payload after generation will invalidate the hash.

## 5. Variance Gateway & HITL

Once the full `todolist.md` is assembled, the Overseer runs the Variance Gateway:

1. For every instruction, call `verify_instruction_lock(ptr, verb, hex_hash)` (which internally uses `verify_cauldron_phase(phase=0)`).
2. If any instruction fails, the entire plan is quarantined. The Overseer:
   * Freezes the state in Numo memory.
   * Updates the `project_state.canvas` JSON, setting the anomalous task node's color to `"red"`.
   * Appends a HITL alert to the active Obsidian Markdown file:

```markdown
> **⚠️ TOPOLOGICAL VARIANCE DETECTED**
> Instruction `[ 0x51 | ⛁ | #A1B2C3D4 ]` failed Cauldron phase-duality verification.
> Possible causes: hallucinated pointer, payload tampering, or secret mismatch.
>
> **Action Required:** [Override & Proceed] | [Rewrite Instruction] | [Abort Plan]
```

**Executive Mode** (read from the project's Obsidian frontmatter) governs the behaviour:

* `manual` → Overseer halts and waits for the Chairperson to type `"Approved"`.
* `proxy-ceo` → Overseer auto-overrides only if the overall variance (percentage of failed instructions) is below the configured `variance_threshold_percent` (default 85%). Otherwise, it falls back to `manual`.

If the human selects **Override & Proceed**, the node is recoloured yellow (override flagged) and the plan proceeds. If they **Rewrite**, the affected Skilled Agent regenerates that instruction.

## 6. Tactical HUD Integration

The Obsidian Canvas (`project_state.canvas`) is the single visual truth for the human operator. Node colour semantics:

| Colour | Meaning                                                                            |
| ------ | ---------------------------------------------------------------------------------- |
| Green  | Verified, ready for execution (or already executed by downstream layer).           |
| Yellow | Override active – human forced a failed instruction through, or an unaudited model was used. |
| Red    | Topological variance detected – execution blocked, human attention required.       |
| Grey   | Planned but not yet verified.                                                      |

The Canvas is updated live by the Overseer via `obsidian-mcp`:

1. After decomposition, all nodes appear as grey.
2. After variance check, nodes turn green or red.
3. After an override, a yellow node is placed.

## 7. Model Registry & Model Auditing

The team respects the `model-registry.md` in Obsidian. Each Skilled Agent is instantiated with a specific model. If the model assigned to any planner is **Unaudited**, the Overseer applies a yellow flag to the entire plan before any variance check. The human must manually approve the use of an unaudited model, regardless of executive mode. This is a safety feature to prevent unchecked models from polluting the KNOTstore instruction space.

## 8. Team Configuration (YAML)

The Core Dev planning team is defined using a minimal operator-kit style YAML, without any robot definitions.

```yaml
team: core_dev_planners
description: "HELIXos Core Development Planning Team – Decomposition and Verification Only"
overseer:
  role: "Lead Architect"
  system_prompt_ref: "HELIXvault/prompts/overseer_base.md"
  capabilities: ["decompose", "verify_instruction_lock", "trigger_hitl", "update_canvas"]
skilled_agents:
  - id: frontend-plan
    role: "Frontend Logic Planner"
    scope: "UI architecture, state flow, component hierarchy"
    prompt_ref: "HELIXvault/prompts/frontend_planner.md"
    allowed_verbs: ["⬢", "⨮"]
    output_file: "todolist_frontend.md"
  - id: backend-plan
    role: "Backend Systems Planner"
    scope: "Server routes, middleware, auth logic"
    prompt_ref: "HELIXvault/prompts/backend_planner.md"
    allowed_verbs: ["⬢", "⛁", "⛟"]
    output_file: "todolist_backend.md"
  - id: db-plan
    role: "Database Schema Architect"
    scope: "SQL/NoSQL schema, migrations, indexing"
    prompt_ref: "HELIXvault/prompts/database_architect.md"
    allowed_verbs: ["⛃", "⛁"]
    output_file: "todolist_database.md"
  - id: api-plan
    role: "API Contract Designer"
    scope: "REST/GraphQL endpoints, DTOs, versioning"
    prompt_ref: "HELIXvault/prompts/api_designer.md"
    allowed_verbs: ["⛮", "⬢"]
    output_file: "todolist_api.md"
# No robot executors defined – planning output is handed over to the execution layer.
```

## 9. Security & Invariants

* **KNOTstore Blackbox:** Only the `KNOT_API_WRAPPER` functions are used. The `.so` is never inspected.
* **No Cross-Agent Data Leak:** Skilled Agents cannot read each other's private `domain_context.md` or output files before the Overseer's final merge.
* **Immutable Pointers:** A Triangulated Instruction's pointer and hash are bound at creation; any change breaks verification.
* **HITL Always Enforced for Anomalies:** Even in proxy-CEO mode, a variance failure above the threshold requires explicit human intervention.

## 10. Validation Tests (OG Features)

1. **Triangulation Unit Test:**
   `generate_triangulated_instruction("build login form", "⬢", key)` returns a valid instruction format; `verify_instruction_lock(...)` returns `True`.
2. **Full Planning Flow Test:**
   Provide a minimal `Council_Ledger.md` ("Create a health tracker PWA with auth"). Verify that:
   * Overseer produces four distinct scopes.
   * Each Skilled Agent outputs only Triangulated Instructions.
   * Variance Gateway passes all checks.
   * Canvas nodes turn green.
3. **HITL Activation Test:**
   Corrupt one hash in a todolist. Confirm that the gateway halts, the Canvas node turns red, and the Markdown alert appears.
4. **Unaudited Model Handling:**
   Set the `model` field of a Skilled Agent to an unaudited model. Confirm a yellow flag is raised and manual override is required.

---

**End of Specification**

*Lead Architect Overseer — Core Dev Planning | HELIXos v2.0, OG Features*

---

## Relationship to the Live-Operation Directive

This planning team produces plans only; it makes no implementation claim and executes nothing. The downstream stateless execution layer it hands off to is the [Digital Operator layer](live-operation-directive.md), which is bound by the Live-Operation Directive: Triangulated Instructions must resolve to complete authorized execution packages before any terminal or browser interaction begins, and completion is claimed only after real, independently verified effects.
