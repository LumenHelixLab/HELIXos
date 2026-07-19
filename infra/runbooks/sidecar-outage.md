# Runbook: knotcore sidecar outage

**Class:** `Unavailable` (ADR-001 §2.4) — circuit-break + retry. **Never** a
Projective Collapse trigger.

**Governing:** ADR-002, SPEC §3.4. Unit: `helix-knotcore-sidecar.service`.

## 1. Detect

1. Client logs show typed failures from `sidecar_client`: `Unavailable` raised
   immediately (breaker open after 5 consecutive errors, per
   `breaker_threshold`), e.g. `sidecar.call failed method=store_instruction
   error=Unavailable`.
2. Health probe fails — run:
   ```bash
   HELIXOS_SIDECAR_SOCKET=${HELIXOS_SIDECAR_SOCKET:-/run/helixos/knotcore.sock} \
   PYTHONPATH=/opt/helixos/aigent-os-kernel/KNOTstore_bin/sidecar \
   python3 -c "from sidecar_client import KnotClient; import os; print(KnotClient(os.environ['HELIXOS_SIDECAR_SOCKET']).health())"
   ```
   Expect `{'status': 'ok', 'abi_version': 1}`. A connection error or timeout
   confirms the outage.
3. Check the unit: `systemctl status helix-knotcore-sidecar` and
   `journalctl -u helix-knotcore-sidecar -n 100 --no-pager`.

## 2. Restart

1. `sudo systemctl restart helix-knotcore-sidecar` — the unit restarts
   `always` with `RestartSec=2`; a manual restart is only needed if systemd
   gave up (start-limit hit) or the process is hung.
2. If hung: `sudo systemctl kill -s SIGKILL helix-knotcore-sidecar` then
   restart (graceful SIGTERM has `TimeoutStopSec=10`).
3. If restarts flap, stop here and escalate with the journal from step 1.3 —
   a crashing simulator/server is a code defect, not an ops event.

## 3. Verify ABI handshake

1. Re-run the health probe (§1 step 2). It MUST return
   `{'status': 'ok', 'abi_version': 1}`; the client refuses mismatched ABI at
   connect (ADR-002 §2.2), so a version mismatch is a boot-time error —
   do not proceed until it matches `sidecar.abi_version` in
   `configs/helixos.yaml`.
2. Round-trip smoke check:
   ```bash
   PYTHONPATH=/opt/helixos/aigent-os-kernel/KNOTstore_bin/sidecar \
   python3 -c "
   from sidecar_client import KnotClient
   import os
   c = KnotClient(os.environ.get('HELIXOS_SIDECAR_SOCKET', '/run/helixos/knotcore.sock'))
   ptr = c.store_instruction('runbook-smoke')
   assert c.fetch_payload(ptr) == 'runbook-smoke'
   print('sidecar OK', ptr)"
   ```
3. Confirm client breakers close: after `breaker_reset_s` (30 s default) the
   next call half-opens; a successful call resets the consecutive-error count.

## 4. Replay journal if needed

1. The sidecar holds payload bodies; the **journal is the record** (ADR-001).
   If the outage window dropped journaled events that required store
   round-trips, replay from the last snapshot marker:
   ```bash
   PYTHONPATH=/opt/helixos/aigent-os-kernel/src/memory \
   python3 -c "
   from journal import EventJournal
   import os
   j = EventJournal(os.environ.get('HELIXOS_JOURNAL_PATH', './helixos-journal.jsonl'))
   assert j.verify_chain(), 'journal chain broken — escalate to projective-collapse.md'
   evts = j.read_all()
   print(f'{len(evts)} events, chain OK; re-apply events after last snapshot marker')"
   ```
2. Re-apply events in `seq` order with pure (state, event) handlers only —
   wall-clock reads during replay are forbidden (ADR-001 §3.2).
3. If `verify_chain()` fails, this is no longer an outage runbook: switch to
   [projective-collapse.md](projective-collapse.md).
