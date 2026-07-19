"""Tests for src/memory/journal.py (SPEC.md §3.7; AUD-C8 / ADR-001)."""

import json
import threading

import pytest

from journal import GENESIS_PREV, EventJournal


class TestAppendRead:
    def test_roundtrip(self, tmp_path):
        j = EventJournal(tmp_path / "j.jsonl")
        j.append("boot", {"kernel": "helix"}, epoch=1)
        j.append("dispatch", {"cmd": "PING"}, epoch=1)
        j.append("collapse", {"reason": "tampered"}, epoch=2)
        records = j.read_all()
        assert [r["seq"] for r in records] == [1, 2, 3]
        assert [r["type"] for r in records] == ["boot", "dispatch", "collapse"]
        assert [r["epoch"] for r in records] == [1, 1, 2]
        assert records[1]["payload"] == {"cmd": "PING"}
        assert all(isinstance(r["ts"], float) for r in records)
        j.close()

    def test_genesis_prev_and_hash_shape(self, tmp_path):
        j = EventJournal(tmp_path / "j.jsonl")
        j.append("boot", {})
        (first,) = j.read_all()
        assert first["prev"] == GENESIS_PREV == "0" * 64
        assert len(first["hash"]) == 64
        int(first["hash"], 16)  # valid hex
        j.close()

    def test_seq_monotonic_across_reopen(self, tmp_path):
        path = tmp_path / "j.jsonl"
        j = EventJournal(path)
        j.append("a", {"n": 1})
        j.append("b", {"n": 2})
        j.close()
        j2 = EventJournal(path)  # O_APPEND|O_CREAT resume: chain continues
        seq = j2.append("c", {"n": 3})
        assert seq == 3
        records = j2.read_all()
        assert [r["seq"] for r in records] == [1, 2, 3]
        assert records[2]["prev"] == records[1]["hash"]
        assert j2.verify_chain() is True
        j2.close()

    def test_append_validation(self, tmp_path):
        j = EventJournal(tmp_path / "j.jsonl")
        for bad_type in ("", None, 5):
            with pytest.raises(ValueError):
                j.append(bad_type, {})
        with pytest.raises(ValueError):
            j.append("x", ["not", "a", "dict"])
        for bad_epoch in (-1, 1.5, "1", True):
            with pytest.raises(ValueError):
                j.append("x", {}, epoch=bad_epoch)
        with pytest.raises(TypeError):  # non-serializable payload never reaches disk
            j.append("x", {"bad": object()})
        assert j.read_all() == []  # failed appends left no partial lines
        j.close()

    def test_append_after_close_raises(self, tmp_path):
        j = EventJournal(tmp_path / "j.jsonl")
        j.close()
        j.close()  # idempotent
        with pytest.raises(ValueError, match="closed"):
            j.append("x", {})

    def test_context_manager(self, tmp_path):
        with EventJournal(tmp_path / "j.jsonl") as j:
            j.append("x", {"v": 1})
        with pytest.raises(ValueError):
            j.append("y", {})


class TestVerifyChain:
    def test_valid_chain(self, tmp_path):
        j = EventJournal(tmp_path / "j.jsonl")
        for i in range(10):
            j.append("event", {"i": i}, epoch=i % 3)
        assert j.verify_chain() is True
        j.close()

    def test_empty_journal_is_vacuously_valid(self, tmp_path):
        j = EventJournal(tmp_path / "j.jsonl")
        assert j.verify_chain() is True
        j.close()

    def _tamper(self, path, mutate):
        lines = (path).read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[1])  # second line
        mutate(record)
        lines[1] = json.dumps(record, sort_keys=True, separators=(",", ":"))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_tampered_payload_breaks_chain(self, tmp_path):
        path = tmp_path / "j.jsonl"
        j = EventJournal(path)
        for i in range(4):
            j.append("event", {"i": i})
        j.close()
        self._tamper(path, lambda r: r["payload"].update(i=999))
        assert EventJournal(path).verify_chain() is False

    def test_tampered_prev_breaks_chain(self, tmp_path):
        path = tmp_path / "j.jsonl"
        j = EventJournal(path)
        for i in range(4):
            j.append("event", {"i": i})
        j.close()
        self._tamper(path, lambda r: r.update(prev="0" * 64))
        assert EventJournal(path).verify_chain() is False

    def test_tampered_seq_breaks_chain(self, tmp_path):
        path = tmp_path / "j.jsonl"
        j = EventJournal(path)
        for i in range(4):
            j.append("event", {"i": i})
        j.close()
        self._tamper(path, lambda r: r.update(seq=42))
        assert EventJournal(path).verify_chain() is False

    def test_truncated_tail_detected_at_open(self, tmp_path):
        path = tmp_path / "j.jsonl"
        j = EventJournal(path)
        for i in range(3):
            j.append("event", {"i": i})
        j.close()
        with open(path, "r+b") as fh:  # chop the last line in half (invalid JSON)
            fh.truncate(fh.seek(0, 2) - 10)
        # fail-closed: a corrupt tail must never be silently resumed/forked
        with pytest.raises(ValueError, match="tail corrupt"):
            EventJournal(path)

    def test_mid_file_garbage_fails_verify_and_read(self, tmp_path):
        path = tmp_path / "j.jsonl"
        j = EventJournal(path)
        for i in range(4):
            j.append("event", {"i": i})
        j.close()
        lines = path.read_text(encoding="utf-8").splitlines()
        lines[1] = "!!! not json !!!"  # tail stays valid so the journal still opens
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        j2 = EventJournal(path)
        assert j2.verify_chain() is False
        with pytest.raises(ValueError, match="corrupt"):
            j2.read_all()
        j2.close()


class TestConcurrency:
    def test_concurrent_appends_all_present_chain_valid(self, tmp_path):
        j = EventJournal(tmp_path / "j.jsonl")
        n_threads, per_thread = 8, 25
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker(tid):
            try:
                barrier.wait(timeout=5)
                for k in range(per_thread):
                    j.append("tick", {"tid": tid, "k": k})
            except Exception as exc:  # noqa: BLE001 - captured for assertion
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        j.close()

        assert errors == []
        records = EventJournal(tmp_path / "j.jsonl").read_all()
        assert len(records) == n_threads * per_thread
        assert [r["seq"] for r in records] == list(range(1, n_threads * per_thread + 1))
        seen = {(r["payload"]["tid"], r["payload"]["k"]) for r in records}
        assert seen == {(t, k) for t in range(n_threads) for k in range(per_thread)}
        assert EventJournal(tmp_path / "j.jsonl").verify_chain() is True

    def test_corrupt_tail_rejected_on_open(self, tmp_path):
        path = tmp_path / "j.jsonl"
        path.write_text('{"seq": 1, "hash": ', encoding="utf-8")  # unparseable tail
        with pytest.raises(ValueError, match="tail corrupt"):
            EventJournal(path)
