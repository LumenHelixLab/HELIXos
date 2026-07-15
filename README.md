# HELIXos

HELIXos is an operating layer for **Digital Operators** — agents that work through real computing environments the same way a competent human operator would at a terminal or web browser.

The present execution layer consists of:

* **Terminal Operators** — shell, filesystem, Git, build/test/validation tooling
* **Browser Operators** — real websites and web applications, DOM-guided and visual interaction
* **Repository Operators** — source control and code-oriented workflows
* **Application Operators** — authorized desktop applications
* **Research Operators** — retrieval of real current information

Physical robots and embodied hardware are future-direction concepts, outside the current implementation scope. The legacy labels `Robot Executor` and `Robot Agent` may persist internally, but all current specifications and user-facing documentation use **Digital Operator**.

## Governing Principle

HELIXos must never confuse a representation of work with completed work. A plan is not execution; a tool call is not success; a generated message is not evidence. A workflow is complete only when the requested effect occurred in the real target environment and that effect was independently verified.

Every Digital Operator follows the loop:

```text
OBSERVE → INTERPRET → ACT → VERIFY → RECORD
```

## Specifications

* [Live-Operation Directive](docs/specs/live-operation-directive.md) — the binding specification for the present system, covering:
  * the prohibition of mock and placeholder content in operational paths
  * human-equivalent terminal and browser operation requirements
  * real-integrations-only adapter and health-check rules
  * the Digital Operator execution model and Triangulated Instruction resolution
  * the Observe–Act–Verify loop
  * human handoff conditions and Tactical HUD requirements
  * the live acceptance standard for browser, terminal, and integration features
  * system status labels (Specified → Production-Ready, Blocked, Not Implemented)
* [Core Development Multi-Agent Planning Team (v2.0)](docs/specs/core-dev-planning-team.md) — the planning layer ("prefrontal cortex") that decomposes Council-approved ledgers into verified plans, covering:
  * the Overseer (Lead Architect) and the four domain planners (frontend, backend, database, API)
  * the OG feature stack: Obsidian Cognitive UI, KNOTstore, Triangulated Instruction Bus, Variance Gateway, Tactical HUD, Pipeline Council integration
  * the four-phase planning lifecycle (decomposition → triangulation → variance gateway & HITL → plan approval)
  * executive modes (`manual`, `proxy-ceo`), Canvas node colour semantics, and model-registry auditing
  * team configuration YAML, security invariants, and validation tests

The planning team produces plans only; execution is handed off to the Digital Operator layer governed by the Live-Operation Directive.

## Design Notes

* [Core Dev Planning Team — Analysis & Design Notes](docs/design/core-dev-planning-team-notes.md) — gap analysis of the v2.0 spec with proposed resolutions (pointer namespace scoping, variance threshold semantics, dependency DAG via Canvas edges, verb registry extensions, key management) and open questions for the Council.

## System Status Labels

| Status             | Meaning                                                                    |
| ------------------ | -------------------------------------------------------------------------- |
| Specified          | Requirements exist; no implementation claim                                |
| Connected          | Real service connection established                                        |
| Read-Validated     | Real data successfully retrieved                                           |
| Write-Validated    | Authorized real change successfully persisted                              |
| Workflow-Validated | Complete real workflow passed                                              |
| Production-Ready   | Security, recovery, observability, and acceptance gates passed             |
| Blocked            | External permission, credential, infrastructure, or human action required  |
| Not Implemented    | Interface or design exists without working behavior                        |

Current repository status: **Specified** — this repository holds the governing specification; no implementation claim is made here.

## License

See [LICENSE](LICENSE).
