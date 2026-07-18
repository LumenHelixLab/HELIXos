"""Signed, hash-chained, append-only audit log (reviewer finding 7)."""

from __future__ import annotations

import os
import sqlite3

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from helix_gate.audit import AuditLog


def _log(tmp_path):
    return AuditLog(os.path.join(tmp_path, "audit.db"),
                    Ed25519PrivateKey.generate(), "audit-key")


def test_events_are_structured_not_strings(tmp_path):
    log = _log(tmp_path)
    ev = log.append(operation_id="op1", sandbox_id="sbx1", state="RUNNING",
                    previous_state="INITIALIZING", reason_code="OK")
    assert ev.fields["state"] == "RUNNING"       # machine-readable
    assert ev.fields["reason_code"] == "OK"
    assert ev.fields["event_version"] == "HX-AUDIT-1"
    assert ev.signature  # signed


def test_chain_verifies_when_intact(tmp_path):
    log = _log(tmp_path)
    for i in range(5):
        log.append(operation_id=f"op{i}", sandbox_id=None, state="RECEIVED",
                   previous_state=None, reason_code="OK")
    assert log.verify_chain() is True
    # digests actually chain: each event references the prior digest.
    events = log.events()
    for prev, cur in zip(events, events[1:]):
        assert cur.fields["previous_event_digest"] == prev.digest


def test_tampering_breaks_the_chain(tmp_path):
    path = os.path.join(tmp_path, "audit.db")
    log = AuditLog(path, Ed25519PrivateKey.generate(), "k")
    log.append(operation_id="op1", sandbox_id=None, state="RECEIVED",
               previous_state=None, reason_code="OK")
    log.append(operation_id="op1", sandbox_id=None, state="REJECTED",
               previous_state="VALIDATING", reason_code="REJECTED_EXPIRED")

    # Tamper with a stored event body out-of-band.
    conn = sqlite3.connect(path)
    conn.execute("UPDATE audit_events SET event_json = REPLACE(event_json, "
                 "'REJECTED_EXPIRED', 'OK') WHERE seq = 1")
    conn.commit()
    conn.close()

    assert log.verify_chain() is False


def test_dropping_an_event_breaks_the_chain(tmp_path):
    path = os.path.join(tmp_path, "audit.db")
    log = AuditLog(path, Ed25519PrivateKey.generate(), "k")
    for i in range(3):
        log.append(operation_id=f"op{i}", sandbox_id=None, state="RECEIVED",
                   previous_state=None, reason_code="OK")
    conn = sqlite3.connect(path)
    conn.execute("DELETE FROM audit_events WHERE seq = 1")  # remove a middle link
    conn.commit()
    conn.close()
    assert log.verify_chain() is False
