# babel-tower — Layers 3/4 (The Distributor / Translation Grid)

The linguistic clutch. Reads `genome.html`, forces agents to shift communication
"gears" (spec §3), enforces the `0x18` CANCEL failsafe, and proxies machine
traffic into human-readable strings.

See [`docs/SPECIFICATION.md` §1 (Layer 4)](../docs/SPECIFICATION.md#layer-4--the-distributor-babel-tower--babel-fish),
[§3](../docs/SPECIFICATION.md#3-the-codex-of-babel-linguistic-gears), and
[§4.3](../docs/SPECIFICATION.md#43-projective-collapse--the-exhaust-system).

## Modules

| File | Responsibility |
|---|---|
| `babel_dispatcher.py` | Triangulated Bus `[ Ptr \| Verb \| Hash ]` parser; `0x18` CANCEL enforcement; Projective Collapse trigger. |
| `lexicon_resolver.yaml` | Maps the §2 agent taxonomy to logic domains. |
| `babelfish.py` | Textual TUI + Obsidian MCP proxy middleware (the Owner's interface). |

`lexicon_resolver.yaml` is the one artifact in this repository populated with
real content (the taxonomy is fully specified in §2); the two Python modules are
documented stubs.
