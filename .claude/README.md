# HELIXos Contributor Toolkit (`.claude/`)

Repo-scoped [Claude Code](https://claude.com/claude-code) tooling that helps
people **build** HELIXos. This is developer tooling — distinct from the runtime
agent taxonomy specified in [`../agents/`](../agents) and
[`../docs/SPECIFICATION.md`](../docs/SPECIFICATION.md). It executes nothing in the
product; it just makes contributing faster.

> Adapted for HELIXos from [**operator-kit**](https://github.com/wrg32786/operator-kit)
> by wrg32786 (MIT). The five specialist roles and the keyword context-loader
> idea come from operator-kit; the agent prompts are rewritten for this repo and
> the loader hook is an original re-implementation. See [NOTICE](NOTICE).

## The five arbiters (`.claude/agents/helix/`)

operator-kit's specialists, renamed to the HELIXos deity tier (the spec's
"Nucleus: long-running arbiters") and re-scoped to this codebase. Each echoes a
Quaternity orchestrator:

| Agent | Role | Tools | Quaternity echo |
|---|---|---|---|
| **ARGUS** (was Echo) | Read-only codebase scout — `path:line` findings, no writes | Read/Grep/Glob | Charlotte (weaver) |
| **OGUN** (was Lyra) | The Smith — bounded code builds + diffs + honesty ledger | Read/Edit/Write/Bash | Krishna (manifestor) |
| **THOTH** (was Newton) | The Scribe — cited research synthesis | Read/Web | Kali (synthesis) |
| **ATHENA** (was Hypatia) | The Strategist — read-only critique of a plan's risks | Read/Grep/Glob | Natasha (red team) |
| **PTAH** (was Iris) | The Architect — design/spec artifacts, not code | Read/Write/Edit | Charlotte (structure) |

Invoke them by name in Claude Code (e.g. "have ARGUS find where reason codes are
defined", "ask ATHENA to critique this approach before I build it").

## Auto-context loader

`hooks/helix-context-loader.sh` (a `UserPromptSubmit` hook, logic in
`hooks/helix_context_loader.py`) watches prompts for HELIXos keywords and injects
the relevant files as context — so you don't re-explain the architecture every
session. Mappings live in [`helix-keywords.json`](helix-keywords.json); edit that
to add your own. It is **failsafe**: it always exits 0 and injects nothing on any
error, and caps injection at ~60 KB.

Example: a prompt mentioning "gate 2" or "hx1" auto-loads `helix_gate/README.md`,
`docs/GATES.md`, and the relevant `hx1/` sources.

## Activation & opt-out

The loader is wired in [`settings.json`](settings.json) and activates for anyone
running Claude Code in this repo. To disable it, delete the `hooks` block from
`.claude/settings.json` (or remove `settings.json`). The agents work with or
without the hook.

## Try it

```bash
echo '{"prompt":"how does gate 2 validate an HX1 envelope?"}' \
  | CLAUDE_PROJECT_DIR="$PWD" bash .claude/hooks/helix-context-loader.sh
```
