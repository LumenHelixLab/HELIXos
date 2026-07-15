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
