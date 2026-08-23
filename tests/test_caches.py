"""Tests for api/caches.py's _SolveCache (M92).

One test module per source module, per this project's convention.
`api/caches.py` had none — its behaviour was covered incidentally through
the endpoints that use it, which is exactly why the thundering-herd cost
below went unmeasured for so long.
"""

import threading
import time

from api.caches import _SolveCache


def test_get_or_compute_runs_the_factory_once_under_concurrency():
    """The single-flight guarantee, and the reason M92 exists.

    Every cache helper used to check the cache, compute UNLOCKED, then
    write — documented as an accepted tradeoff because these solves are
    deterministic, so whichever racer wins is correct. That reasoning is
    about CORRECTNESS and it holds. It quietly accepted an N-times COST
    nobody had measured: 8 concurrent requests sharing one solve key ran
    8 full solves (223s), where the same 8 sequentially ran 0.
    """
    cache = _SolveCache("test_single_flight")
    calls = []
    calls_lock = threading.Lock()

    def factory():
        with calls_lock:
            calls.append(1)
        time.sleep(0.05)  # long enough that every thread is inside the miss
        return "solved"

    results = []
    results_lock = threading.Lock()

    def worker():
        value = cache.get_or_compute("same-key", factory)
        with results_lock:
            results.append(value)

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(calls) == 1, f"factory ran {len(calls)} times; single-flight means once"
    assert results == ["solved"] * 12, "every caller must get the winner's value"
    assert cache.entries["same-key"] == "solved"


def test_get_or_compute_does_not_serialize_different_keys():
    """The lock discipline that is easy to get wrong: `self.lock` must
    guard only the dict, never a solve. Holding it across the factory
    would make concurrent misses on DIFFERENT keys queue behind each
    other for no reason — trading one performance bug for another.
    """
    cache = _SolveCache("test_parallel_keys")
    barrier = threading.Barrier(4, timeout=5)

    def factory():
        # Every distinct key must be able to be inside its factory at the
        # same moment; if they serialize, this barrier times out.
        barrier.wait()
        return "done"

    errors = []

    def worker(key):
        try:
            cache.get_or_compute(key, factory)
        except Exception as exc:  # BrokenBarrierError on serialization
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(f"key-{i}",)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, f"different keys serialized against each other: {errors}"


def test_get_or_compute_serves_later_callers_from_the_cache():
    cache = _SolveCache("test_reuse")
    calls = []
    value = cache.get_or_compute("k", lambda: calls.append(1) or "v")
    again = cache.get_or_compute("k", lambda: calls.append(1) or "other")
    assert value == "v" and again == "v"
    assert len(calls) == 1


def test_get_or_compute_does_not_leak_a_lock_per_key():
    """Per-key locks are dropped once their solve lands. Keeping them
    would mean one lock object retained forever per distinct key ever
    requested — a slow leak on a long-running server, which is exactly
    what this product is."""
    cache = _SolveCache("test_no_leak")
    for i in range(50):
        cache.get_or_compute(f"key-{i}", lambda: "v")
    assert len(cache.entries) == 50
    assert cache._key_locks == {}, "per-key locks must not accumulate"


def test_clear_drops_in_flight_key_locks_too():
    cache = _SolveCache("test_clear")
    cache.get_or_compute("k", lambda: "v")
    cache._key_locks["stale"] = threading.Lock()
    cache.clear()
    assert cache.entries == {}
    assert cache._key_locks == {}


def test_a_failing_factory_does_not_poison_the_key():
    """If a solve raises, the next caller must be able to try again
    rather than deadlocking on a lock nobody released or inheriting a
    half-written entry."""
    cache = _SolveCache("test_failure")
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("solve blew up")
        return "recovered"

    try:
        cache.get_or_compute("k", flaky)
    except RuntimeError:
        pass
    assert "k" not in cache.entries, "a failed solve must not be cached"
    assert cache.get_or_compute("k", flaky) == "recovered"


def test_store_and_get_are_safe_while_already_holding_the_lock():
    """M93 regression. Many call sites hold `cache.lock` across a whole
    read-check-write block and call `store` / `get` from inside it. With
    a plain threading.Lock that self-deadlocks — and it did: converting
    the 11 direct `.entries[...] = ...` writes to `store()` hung the
    entire suite until the lock became reentrant.

    Guarded here rather than left to the next person to rediscover, since
    the failure mode is a hang rather than an error and gives no clue
    where to look.
    """
    cache = _SolveCache("test_reentrant", maxsize=4)
    with cache.lock:
        cache.store("k", "v")
        assert cache.get("k") == "v"
        assert cache.setdefault("k2", "v2") == "v2"
        cache.touch("k")
    assert cache.entries["k"] == "v"


def test_maxsize_evicts_least_recently_used_not_least_recently_written():
    """The point of LRU over FIFO: an entry in constant use must survive.
    Reads go through `get`, which marks recency — reading `.entries`
    directly would not, and a hot entry would age out as if cold."""
    cache = _SolveCache("test_lru_order", maxsize=3)
    for key in ("a", "b", "c"):
        cache.store(key, key)
    cache.get("a")          # 'a' is now the most recently used
    cache.store("d", "d")   # evicts the true LRU, which is 'b'
    assert set(cache.entries) == {"a", "c", "d"}


def test_unbounded_caches_keep_everything():
    """maxsize=None is a real choice, not an oversight: _multiway_cache
    and _preflop_raw_cache hold a few dozen entries that cost 75-140s
    each and are pre-warmed at startup. Evicting one throws away work the
    server did precisely so a user would not wait for it."""
    cache = _SolveCache("test_unbounded")
    for i in range(200):
        cache.store(i, i)
    assert len(cache.entries) == 200
