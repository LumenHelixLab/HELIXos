# HELIXos Runbooks (M0)

Operational procedures for the HELIXos platform. Part of the audit §7 item 7 /
AUD-H8 platform scaffolding. Each runbook is numbered-step, copy-pasteable,
and written against the SPEC §3 module APIs and the ADR-001 §2.4 error-class
policy (`Unavailable` → circuit-break/retry; `Tampered` → collapse + alert;
`SchemaError` → quarantine).

| Runbook | When to use | Governing docs |
|---|---|---|
| [sidecar-outage.md](sidecar-outage.md) | knotcore sidecar down, circuit breaker open, ABI handshake failure | ADR-002, SPEC §3.4 |
| [projective-collapse.md](projective-collapse.md) | `Tampered`-class verdict: tag mismatch, journal hash-chain break, generation mismatch on a live pointer | ADR-001 §2.3–2.4, SPEC §3.7–3.8 |
| [key-rotation.md](key-rotation.md) | Scheduled or emergency rotation of `HELIXOS_HMAC_KEY` (dev) / Ed25519 rollout to production | ADR-004 §3–4 |

Conventions:

- `journalctl -u <unit> -f` is the first diagnostic stop for every service;
  units live in `infra/systemd/`.
- The journal (`$HELIXOS_JOURNAL_PATH`, default `./helixos-journal.jsonl`) is
  the crown jewel (ADR-001 §3.2): never edit, move, or truncate it outside a
  runbook step; back it up before any recovery procedure.
- Repo checkout on hosts: `/opt/helixos`. Local development: repo root.
