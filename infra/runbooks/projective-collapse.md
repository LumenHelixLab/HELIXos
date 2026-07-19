# Runbook: Projective Collapse (controlled node reset)

**Class:** `Tampered` (ADR-001 §2.4) — collapse + alert. Scope is **per-node**;
a system-wide collapse requires an owner-signed command, journaled as such.

**Governing:** ADR-001 §2.3 (protocol), SPEC §3.7 (`EventJournal`), SPEC §3.8
(`EpochFence`).

## 1. Trigger conditions (collapse ONLY on Tampered)

Execute this runbook iff any of these fire on this node:

1. **Tag/signature mismatch** — `verify_instruction_lock` fails on an
   instruction that passed `BUS_RE` (forgery or corruption; ADR-004).
2. **Journal hash-chain break** — `EventJournal.verify_chain()` returns False.
3. **Generation-counter mismatch on a live pointer** —
   `verify_cauldron_phase(ptr, tag, phase)` fails where the pointer exists and
   no sanctioned `bump_generation` was recorded.

Do NOT collapse for `Unavailable` (sidecar/IRCd transient — use
[sidecar-outage.md](sidecar-outage.md)) or `SchemaError` (malformed line —
quarantine and continue). Before step 2: quarantine the offending instruction
bytes as evidence and page the owner.

## 2. Increment epoch

```bash
PYTHONPATH=/opt/helixos/aigent-os-kernel/src/memory python3 - <<'EOF'
import os
from epochs import EpochFence
from journal import EventJournal

fence = EpochFence()          # load persisted epoch in production wiring
new_epoch = fence.increment() # monotonic: never decreases, never resets
j = EventJournal(os.environ.get("HELIXOS_JOURNAL_PATH", "./helixos-journal.jsonl"))
seq = j.append("epoch.increment", {"epoch": new_epoch}, epoch=new_epoch)
print("epoch", new_epoch, "journaled at seq", seq)
EOF
```

## 3. Rebuild from journal

1. Discard all volatile state: zero-copy cache, in-memory views, seen-nonce
   cache. Derived stores (`Council_Ledgers.md`, Obsidian views) are NOT
   repaired — they are re-rendered later (ADR-001 §2.2).
2. Locate the latest snapshot marker (journaled at least every 10,000 events
   or 24 h) and restore it.
3. Replay forward from that marker, verifying the chain as you go:
   ```bash
   PYTHONPATH=/opt/helixos/aigent-os-kernel/src/memory python3 - <<'EOF'
   import os
   from journal import EventJournal
   j = EventJournal(os.environ.get("HELIXOS_JOURNAL_PATH", "./helixos-journal.jsonl"))
   if not j.verify_chain():
       raise SystemExit("chain broken past snapshot — restore journal from backup FIRST")
   for evt in j.read_all():      # in practice: events after the snapshot marker
       apply_event(evt)          # pure (state, event); NO wall-clock reads (ADR-001 §3.2)
   print("rebuild complete")
   EOF
   ```
   A chain failure during replay is itself a `Tampered` event: stop, restore
   the journal from backup, and re-run from step 2.

## 4. Broadcast epoch

1. Announce the new epoch on the bus (`#t-gateway` at M2; in-process at M0/M1).
2. Stamp the new epoch on every outbound message henceforth; journal a
   `collapse.complete` event with `{epoch, from_snapshot_seq, replayed_count}`.

## 5. Verify fencing

1. Every peer runs `EpochFence.fences(epoch)`: any message with
   `epoch < current` is stale → dropped and logged, never processed
   (SPEC §3.8). This is the split-brain defense.
2. Functional check — send a synthetic message stamped with the pre-collapse
   epoch and confirm it is fenced (dropped + log line) on every peer:
   ```python
   assert fence.fences(old_epoch) is True    # stale → fenced
   assert fence.fences(new_epoch) is False   # current → processed
   ```
3. Confirm no pre-collapse state leaked: regenerated views render identically
   from the replayed journal (hash-compare before/after renders).
4. Close out: attach the quarantined evidence and the `collapse.*` journal
   seqs to the incident record. The M3 chaos drill replays this runbook end
   to end (audit §7, M3 exit criteria).
