# HELIXvault

Agent-side toolbelt vault (Layer C, glossary §4). **M0 status: skeleton only —
no code here yet.** The three subdirectories are reserved homes, each landing
in a later milestone per the audit §7 roadmap:

| Directory | Purpose | Lands |
|---|---|---|
| `babel-lang/` | Shorthand / AFSK translator modules (glossary §5.3) — compact encodings over the Triangulated Bus, distinct from `aigent-os-kernel/src/BABEL/` (the dispatch protocol, SPEC §3.5) | M2 |
| `robot-agents/` | Homes of the four Quaternity agents — NATASHA & CHARLOTTE (Spiders), KALI & KRISHNA (Gods) — with per-agent contracts: responsibilities, I/O schemas, channels, lifecycle, failure modes (glossary §4.1–4.2; audit AUD-H9) | M3 |
| `prompts/` | Versioned prompt/persona libraries for the agents. Prompt text is configuration, never an authorization source (docs/security-model.md §7) | M3 |

M0 anchors these agents depend on already live in the kernel:
`orchestrator/possession.py` (KRISHNA-only possession, SPEC §3.6) and
`src/BABEL/dispatcher.py` (command grammar, SPEC §3.5).
