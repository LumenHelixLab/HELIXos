# agents — LLM Wrappers (LangGraph / LiteLLM)

Cognitive wrappers for the Quaternity orchestrators and the X-Men mutation tier.
See [`docs/SPECIFICATION.md` §2](../docs/SPECIFICATION.md#2-the-universal-symbolic-lexicon-agent-taxonomy).

| File | Persona / tier | Role |
|---|---|---|
| `natasha_agent.py` | 🔴 NATASHA | Red Spider API-gateway defense |
| `charlotte_agent.py` | 🟢 CHARLOTTE | Green context weaver |
| `kali_arbiter.py` | 🟣 KALI | Purple synthesizer / arbiter |
| `x_men_mutators.py` | X-Men tier | Irregular logic / prompt structures |

Each module is a documented stub defining its intended role, the IRC channel
class it operates in, and the CLI verbs it responds to (`!FORK`, `!MUTATE`,
`!ASSEMBLE`, `!POSSESS` — spec §4.2). No LLM wiring is implemented; model choice
and orchestration graph are deferred to implementation.
