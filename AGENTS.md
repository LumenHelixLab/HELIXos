# AGENTS.md

Context for AI coding agents working in this repo. Read this first.

## What this is

HELIXos is a four-tier multi-agent OS **specification** with exactly one boundary
implemented for real: **Gate 2**, the hardened Wasm execution adapter in
`helix_gate/`. Everything else is documented stubs.

## Folder map

- `helix_gate/` — ✅ real, tested. The execution trust boundary (HX1 validation →
  process-isolated Wasm sandbox → signed audit log). Start at `helix_gate/README.md`.
- `tests/` — Gate 2 conformance + fault-injection suite (pytest).
- `docs/SPECIFICATION.md` — the v3.0 architecture spec. `docs/GATES.md` — the
  security-gate model + reviewer disposition.
- `aigent-os-kernel/`, `helix-irc-dmz/`, `babel-tower/`, `agents/`,
  `obsidian-brain/` — ⚠️ stubs. Modules raise `NotImplementedError`.

## Build / test / run

```bash
pip install -r requirements.txt
pytest -q                    # 48 tests, ~5s
python -m helix_gate.demo    # end-to-end + fault-injection tour
```

## Contributor subagents

`.claude/agents/helix/` defines five specialist Claude Code subagents you can
delegate to: **ARGUS** (read-only scout), **OGUN** (bounded builder), **THOTH**
(cited research), **ATHENA** (read-only critique), **PTAH** (design/spec). A
`UserPromptSubmit` hook (`.claude/hooks/`) auto-loads relevant files by keyword.
See `.claude/README.md`.

## Hard constraints

- **Never claim unimplemented behavior works.** Stubs stay honest; a component is
  "implemented" only once it has a conformance + fault-injection test suite.
- **No fabricated metrics.** Any number needs a reproduction path.
- **Gate 2 is security-sensitive.** Preserve: signature-before-trust ordering,
  deny-by-default capabilities, stable reason codes with no exception-text leak
  to callers (diagnostics go to the audit log only), and retained (never deleted)
  registry records.
- Match surrounding code style; keep `helix_gate/` auditable over clever.
- Tests use `multiprocessing` spawn — code that executes Wasm must run under a
  real file / pytest, not piped stdin.
