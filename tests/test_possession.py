"""Tests for orchestrator/possession.py (SPEC.md §3.6; AUD-C2/C3/M4 + fencing)."""

import threading

import pytest

from dispatcher import create_default_dispatcher
from possession import (
    OWNER_TOKEN_ENV,
    KrishnaManifestor,
    PossessionDenied,
    PossessionError,
)

TOKEN = b"correct-horse-battery-staple"
TOKEN_HASH = KrishnaManifestor.hash_token(TOKEN)


def make_manifestor(audit=None, **kwargs):
    return KrishnaManifestor(
        "KRISHNA", TOKEN_HASH, create_default_dispatcher(), audit=audit, **kwargs
    )


class TestConstruction:
    def test_krishna_only(self):
        for bad in ("SHIVA", "krishna", "", "KRISHNA2"):
            with pytest.raises(ValueError, match="KRISHNA"):
                KrishnaManifestor(bad, TOKEN_HASH, create_default_dispatcher())

    def test_token_hash_must_be_sha256_digest(self):
        for bad in (b"short", b"x" * 31, b"x" * 33, "not-bytes"):
            with pytest.raises(ValueError, match="SHA-256"):
                KrishnaManifestor("KRISHNA", bad, create_default_dispatcher())

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv(OWNER_TOKEN_ENV, TOKEN.decode())
        m = KrishnaManifestor.from_env("KRISHNA", create_default_dispatcher())
        assert m.toggle_possession(TOKEN).startswith("MANIFESTOR_MODE: True")

    def test_from_env_missing(self, monkeypatch):
        monkeypatch.delenv(OWNER_TOKEN_ENV, raising=False)
        with pytest.raises(ValueError, match=OWNER_TOKEN_ENV):
            KrishnaManifestor.from_env("KRISHNA", create_default_dispatcher())


class TestAuthentication:
    def test_wrong_token_denied_and_audited(self):
        records = []
        m = make_manifestor(audit=records.append)
        with pytest.raises(PossessionDenied, match="invalid owner token"):
            m.toggle_possession(b"attacker-controlled")
        assert m.possessed_by_owner is False
        assert m.fencing_token == 0  # fence never advances on failure
        assert any("possession.denied agent=KRISHNA" in r for r in records)

    def test_none_and_empty_tokens_denied(self):
        m = make_manifestor()
        for bad in (None, "", b""):
            with pytest.raises(PossessionDenied):
                m.toggle_possession(bad)
        assert m.possessed_by_owner is False

    def test_correct_token_toggles_and_fence_increments(self):
        records = []
        m = make_manifestor(audit=records.append)
        r1 = m.toggle_possession(TOKEN)
        assert r1 == "MANIFESTOR_MODE: True FENCE: 1"
        assert m.possessed_by_owner is True
        assert m.fencing_token == 1
        r2 = m.toggle_possession(TOKEN)
        assert r2 == "MANIFESTOR_MODE: False FENCE: 2"
        assert m.possessed_by_owner is False
        assert m.fencing_token == 2
        toggles = [r for r in records if r.startswith("possession.toggled")]
        assert toggles == [
            "possession.toggled agent=KRISHNA state=True fence=1",
            "possession.toggled agent=KRISHNA state=False fence=2",
        ]


class TestManifest:
    def test_unpossessed_manifest_denied_never_silent_none(self):
        records = []
        m = make_manifestor(audit=records.append)
        with pytest.raises(PossessionDenied, match="not possessed"):
            m.manifest("PING")
        assert any("manifest.refused" in r and "not_possessed" in r for r in records)

    def test_possessed_manifest_dispatches(self):
        records = []
        m = make_manifestor(audit=records.append)
        m.toggle_possession(TOKEN)
        assert m.manifest("PING") == "PONG"
        assert m.manifest("ECHO hello") == "hello"
        dispatches = [r for r in records if r.startswith("manifest.dispatch")]
        assert any("cmd='PING'" in r and "fence=1" in r for r in dispatches)

    def test_manifest_command_validation(self):
        m = make_manifestor()
        m.toggle_possession(TOKEN)
        for bad in ("PING\nPRIVMSG #ops :x", "PING\rMODE", "PING\x00", "", "   ", 42):
            with pytest.raises(PossessionError):
                m.manifest(bad)
        with pytest.raises(PossessionError, match="too long"):
            m.manifest("ECHO " + "A" * 400)

    def test_possession_denied_is_possession_error(self):
        assert issubclass(PossessionDenied, PossessionError)


class TestRateLimit:
    def test_sixth_bad_token_rate_limited_with_defaults(self):
        records = []
        m = make_manifestor(audit=records.append)  # SPEC default: 5 / 300 s
        for _ in range(5):
            with pytest.raises(PossessionDenied, match="invalid owner token"):
                m.toggle_possession(b"wrong")
        with pytest.raises(PossessionDenied, match="rate limited"):
            m.toggle_possession(b"wrong")
        assert any("possession.rate_limited" in r for r in records)

    def test_rate_limit_with_small_ctor_params(self):
        m = make_manifestor(rate_limit=2, rate_window=60.0)
        for _ in range(2):
            with pytest.raises(PossessionDenied, match="invalid owner token"):
                m.toggle_possession(b"wrong")
        with pytest.raises(PossessionDenied, match="rate limited"):
            m.toggle_possession(b"wrong")
        # Even the CORRECT token is throttled once the limit trips (brute-force defense)
        with pytest.raises(PossessionDenied, match="rate limited"):
            m.toggle_possession(TOKEN)

    def test_invalid_rate_params_rejected(self):
        with pytest.raises(ValueError):
            make_manifestor(rate_limit=0)
        with pytest.raises(ValueError):
            make_manifestor(rate_window=0)


class TestConcurrency:
    def test_ten_threads_toggle_deterministic_parity(self):
        m = make_manifestor()
        results, errors = [], []
        barrier = threading.Barrier(10)

        def worker():
            try:
                barrier.wait(timeout=5)
                results.append(m.toggle_possession(TOKEN))
            except Exception as exc:  # noqa: BLE001 - captured for assertion
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert errors == []
        assert len(results) == 10
        # 10 toggles from False -> deterministic final state; every fence 1..10 exactly once
        assert m.possessed_by_owner is False
        assert m.fencing_token == 10
        fences = sorted(int(r.rsplit("FENCE: ", 1)[1]) for r in results)
        assert fences == list(range(1, 11))

    def test_concurrent_manifest_refusals_all_raise(self):
        m = make_manifestor()
        errors = []

        def worker():
            try:
                m.manifest("PING")
            except PossessionDenied:
                errors.append("denied")

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        assert errors == ["denied"] * 8
        assert m.possessed_by_owner is False
