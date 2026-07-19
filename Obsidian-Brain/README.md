# Obsidian-Brain

The human cognitive UI of HELIXos (Layer D, glossary §4): an Obsidian vault
where the owner reads agent deliberations (`Council_Ledgers.md`), the
KNOTstore instruction log (`Akosh_Registry.md`), and active project state
(`Active_Projects/`).

## Write discipline (binding, ADR-001 §2.2 / AUD-H9)

- **vault-bridge is the SOLE WRITER of this vault.** It subscribes to journal
  events and renders markdown views; every view is regenerable from the
  journal and is re-rendered, never merged or hand-repaired.
- **Humans write ONLY to `inbox/`.** Files dropped there are journaled as
  input events and processed by the system. Editing any other file in the
  vault fights the renderer and your edits will be overwritten.
- No agent, and no human, edits `Council_Ledgers.md`, `Akosh_Registry.md`, or
  `Active_Projects/` directly — the v2.0 two-writers-one-file failure mode
  (AUD-C8) is closed by construction.

## Status

M0: skeleton — `inbox/` exists and is ready for human input. The vault-bridge
service (`infra/systemd/helix-vault-bridge.service`) activates at **M3**; the
rendered views land with it.
