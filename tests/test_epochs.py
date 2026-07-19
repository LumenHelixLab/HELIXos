"""Tests for src/memory/epochs.py (SPEC.md §3.8; AUD-C8 Projective Collapse fencing)."""

import threading

import pytest

from epochs import EpochFence


class TestFencingSemantics:
    def test_default_epoch_zero_fences_nothing(self):
        fence = EpochFence()
        assert fence.current == 0
        assert fence.fences(0) is False  # current epoch is never stale
        assert fence.fences(5) is False  # future epoch is not stale

    def test_increment_returns_new_epoch(self):
        fence = EpochFence()
        assert fence.increment() == 1
        assert fence.current == 1
        assert fence.increment() == 2
        assert fence.current == 2

    def test_fences_true_exactly_when_stale(self):
        fence = EpochFence(3)
        assert fence.fences(0) is True   # stale: older than current
        assert fence.fences(2) is True   # stale
        assert fence.fences(3) is False  # current: live
        assert fence.fences(4) is False  # future: not fenced by this node

    def test_collapse_cycle(self):
        """collapse -> increment -> rebuild -> broadcast -> peers fence stale."""
        node = EpochFence()
        seen_epoch = node.current  # peer observes broadcast
        assert node.fences(seen_epoch) is False
        node.increment()  # Projective Collapse triggered
        assert node.fences(seen_epoch) is True  # pre-collapse traffic fenced
        assert node.fences(node.current) is False

    def test_custom_start_epoch(self):
        assert EpochFence(42).current == 42

    def test_invalid_epochs_rejected(self):
        for bad in (-1, 1.5, "0", None, True):
            with pytest.raises(ValueError):
                EpochFence(bad)
        fence = EpochFence()
        for bad in (1.5, "1", None, False):
            with pytest.raises(ValueError):
                fence.fences(bad)


class TestThreadSafety:
    def test_concurrent_increments_are_monotonic(self):
        fence = EpochFence()
        n_threads, per_thread = 10, 100
        barrier = threading.Barrier(n_threads)
        results, errors = [], []
        lock = threading.Lock()

        def worker():
            try:
                barrier.wait(timeout=5)
                for _ in range(per_thread):
                    with lock:  # collect without interleaving the list append
                        results.append(fence.increment())
            except Exception as exc:  # noqa: BLE001 - captured for assertion
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert errors == []
        total = n_threads * per_thread
        assert fence.current == total
        assert sorted(results) == list(range(1, total + 1))  # no lost updates, no dupes
