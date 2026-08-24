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


def test_no_solve_cache_is_unbounded():
    """M104. Every solve cache must have a ceiling, because every cache
    key in this app is reachable from a request.

    Two were deliberately unbounded, justified as "at most a few dozen
    entries ever exist, filled by the startup pre-warm". That describes
    the entries the PRE-WARM creates, not the key: both are keyed on
    `round(stack_bb)`, which comes from the client. A caller walking
    stack depths mints a new entry per integer depth forever, measured at
    **0.152 MB of heap per distinct depth**.

    It also composes with a finding that was correct in isolation: M102
    measured `stack_bb: 1e9` as absurd-but-valid and left it uncapped,
    since the response is structurally right and no principled ceiling
    exists. Fine alone; feeding an unbounded cache, it is a
    memory-exhaustion path. **Neither is a defect by itself**, which is
    why this test asserts the invariant over ALL caches rather than
    naming the two that were wrong — the next one added will be wrong the
    same way.
    """
    from api import caches as caches_module

    # Inspect the MODULE's own caches, not `_SolveCache.registered()`.
    # The registry is class-level and shared, so it also contains every
    # throwaway cache the tests above construct — asserting over it would
    # make this test fail for reasons that have nothing to do with the app.
    module_caches = [
        value
        for value in vars(caches_module).values()
        if isinstance(value, caches_module._SolveCache)
    ]
    assert module_caches, "found no caches to check — has the module layout changed?"

    # `multiway_equity` is keyed by the HAND POOL, which is a config
    # constant rather than anything a request supplies, so its key space
    # is effectively one entry and a ceiling would add nothing. Listed
    # explicitly with the reason, so that adding a name here stays a
    # decision rather than a shrug.
    keyed_by_config_not_request = {"multiway_equity"}

    unbounded = [
        c.name
        for c in module_caches
        if c.maxsize is None and c.name not in keyed_by_config_not_request
    ]
    assert not unbounded, (
        f"unbounded solve caches with request-reachable keys: {unbounded}. "
        "An unbounded cache keyed on request input is unbounded growth."
    )

    # M124 (D3): the exemption above must VERIFY its own premise, not
    # just state it. `multiway_equity` is exempt because its only caller
    # passes a config constant — if someone ever passes a request-derived
    # pool, the exemption becomes false silently and this test would
    # otherwise keep passing. That is the F27 shape M104 already fixed
    # twice: a cache justified by what fills it today rather than by what
    # its key admits.
    import inspect
    import re

    from api import solving as solving_module

    source = inspect.getsource(solving_module)
    call_sites = re.findall(r"(?<!def )_get_multiway_equity_cache\(([^)]*)\)", source)
    call_sites = [arg.strip() for arg in call_sites if arg.strip()]
    assert call_sites, "found no call site to check — has the helper been renamed?"
    for arg in call_sites:
        assert arg.startswith("cfg."), (
            f"_get_multiway_equity_cache is called with {arg!r}, which is not a config "
            "constant. Its cache is deliberately unbounded ONLY because the hand pool "
            "cannot vary per request — if that stops being true, give it a maxsize."
        )


def test_the_expensive_caches_keep_a_generous_ceiling():
    """The other half: bounding them must not make them useless.

    Multiway entries cost 75-140s to rebuild and the pre-warm fills them
    on purpose, so the ceiling has to bind only under adversarial
    variety — never under the handful of depths a real session touches.
    Asserted as a floor on the limit so a later "tidy-up" cannot quietly
    shrink it to something that evicts pre-warmed work.
    """
    from api import caches as caches_module

    by_name = {c.name: c for c in caches_module._SolveCache.registered()}
    assert by_name["multiway"].maxsize >= 32
    assert by_name["preflop_raw"].maxsize >= 64
