"""Tests for api/caches.py's _SolveCache (M92).

One test module per source module, per this project's convention.
`api/caches.py` had none — its behaviour was covered incidentally through
the endpoints that use it, which is exactly why the thundering-herd cost
below went unmeasured for so long.
"""

import threading
import time

from fastapi.testclient import TestClient

import api.main as api_main
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

    Multiway entries cost 35-525s to rebuild and the pre-warm fills them
    on purpose, so the ceiling has to bind only under adversarial
    variety — never under the handful of depths a real session touches.
    Asserted as a floor on the limit so a later "tidy-up" cannot quietly
    shrink it to something that evicts pre-warmed work.

    **The floor is DERIVED, not a constant, and that is M215's
    correction.** It used to be a bare `>= 32`, chosen before anyone had
    measured what a multiway entry costs. At the real worst entry
    (256.56 MB, 9-max, pruned) 32 entries is an **8.2 GB** cache, so the
    number protecting against over-eviction was simultaneously licensing
    an enormous overrun. What matters is that the prewarm's own entries
    survive, which is a quantity the config knows.
    """
    from api import caches as caches_module
    from api import config as api_config

    by_name = {c.name: c for c in caches_module._SolveCache.registered()}
    designed = len(api_config.MULTIWAY_PREWARM_STACK_DEPTHS) * 3
    assert by_name["multiway"].maxsize >= designed, (
        f"the multiway ceiling ({by_name['multiway'].maxsize}) is below the "
        f"{designed} entries the prewarm creates, so warmed solves would be "
        f"evicted by their own warm-up")
    assert by_name["preflop_raw"].maxsize >= 64


def _deep_size(obj, seen=None):
    """Bytes an object graph actually holds, following containers and
    counting numpy buffers by `nbytes` rather than by object header."""
    import sys

    import numpy as np

    if seen is None:
        seen = set()
    if id(obj) in seen:
        return 0
    seen.add(id(obj))
    if isinstance(obj, np.ndarray):
        return obj.nbytes
    size = sys.getsizeof(obj, 0)
    if isinstance(obj, dict):
        for key, value in obj.items():
            size += _deep_size(key, seen) + _deep_size(value, seen)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for item in obj:
            size += _deep_size(item, seen)
    elif hasattr(obj, "__dict__"):
        size += _deep_size(vars(obj), seen)
    return size


# M215. No cache is allowed to exceed its byte budget here any more.
#
# M214 added a `_KNOWN_OVER_BUDGET` allowance for `multiway` and it was
# **dead code**: the sweep below sends `players` 2 and 3 only, so that
# cache holds a 2.02 MB 3-max entry and never approached the budget.
# Deleting the allowance entirely changed nothing, which is how it was
# caught. An exception that cannot fire is worse than no exception - it
# reads as a guarded risk while guarding nothing.
#
# The real risk it was meant to describe (a 9-max entry at 256.56 MB) is
# not reachable from a test: that solve costs ~525s. It is pinned by
# `test_the_multiway_preflop_ceiling_is_derived_from_its_worst_entry`
# against a RECORDED measurement instead, which is honest about being a
# recorded number rather than a live one.


def _allowance(_name):
    from api import caches as caches_module
    return caches_module.MAX_CACHE_BYTES_PER_CACHE


def test_cache_ceilings_are_sized_against_what_an_entry_actually_costs():
    """M127. The bound M93/M104 established — and M124 re-verified — is on
    entry COUNT. Entry SIZE is not bounded and varies by ~38x between
    cache types, so uniform 128-256 ceilings were assigned as though
    entries were interchangeable.

    Found by playing rather than by inspection: a simulated session
    grew the working set **linearly at 1.4 MB/s with no plateau** — 1,642
    MB to 3,644 MB over 23 minutes. Fifteen audit rounds and two
    diagnostics missed it because none of them played a hand; it only
    appears under sustained traffic with a fresh board every time, which
    is exactly what a real player generates.

    Measured per entry: `preflop_raw` 0.20 MB, `path_query_libraries`
    0.07 MB, `turn_path` **7.59 MB**. At its 128 ceiling that one cache
    is a ~971 MB budget on its own.

    This test measures a real entry for whichever caches the sweep below
    populates and asserts the resulting byte budget stays under
    `MAX_CACHE_BYTES_PER_CACHE`. It is the assertion the count-bound
    never made.
    """
    from api import caches as caches_module

    caches_module._SolveCache.clear_all()

    # Populate a representative spread: preflop, flop, turn, river.
    base = {"stack_bb": 100.0, "preflop_action_path": ["raise", "call_or_check"],
            "players": 2, "hero_cards": "AsKs"}
    with TestClient(api_main.app) as populating:
        populating.post("/advise", json={**base, "preflop_action_path": []})
        populating.post("/advise", json={**base, "board": "3d7s2c"})
        turn = {**base, "board": "3d7s2c",
                "flop_action_path": ["call_or_check", "call_or_check"], "turn_card": "Kd"}
        populating.post("/advise", json=turn)
        populating.post("/advise", json={**turn,
                                         "turn_action_path": ["call_or_check", "call_or_check"],
                                         "river_card": "9s"})
        # M214: a MULTIWAY request too. Every multiway cache had been
        # outside this check since it was written, because the sweep was
        # entirely `players: 2` — and when one was finally sent, three of
        # them were over budget, one by 31x. Costs ~38s (the multiway
        # preflop solve), which is the price of the guarantee actually
        # covering what it claims to.
        mw = {"stack_bb": 100.0, "players": 3, "hero_cards": "AsKs",
              "board": "3d7s2c",
              "preflop_action_path": ["raise", "call_or_check", "call_or_check"]}
        populating.post("/advise", json=mw)
        populating.post("/advise", json={**mw, "turn_card": "Kd",
                                         "flop_action_path": ["call_or_check"] * 3})
        # the GET routes fill a different set of caches than /advise does
        for url in ("/solve_flop?board=3d7s2c&iterations=40",
                    "/solve_flop_turn?board=3d7s2c&iterations=40"):
            populating.get(url)

    seen, over_budget, measured = set(), [], []
    for value in vars(caches_module).values():
        if not isinstance(value, caches_module._SolveCache) or id(value) in seen:
            continue
        seen.add(id(value))
        if not value.entries or value.maxsize is None:
            continue
        # M215: the WORST entry, not an arbitrary one. `next(iter(...))`
        # judged a cache by whichever entry happened to be first, and
        # entries inside one cache can differ by 127x (`multiway` spans
        # 2.02 MB at 3-max to 256.56 MB at 9-max), so that was a coin
        # flip dressed as a measurement.
        entry_bytes = max(_deep_size(entry) for entry in value.entries.values())
        budget = entry_bytes * value.maxsize
        measured.append((value.name, entry_bytes, value.maxsize, budget))
        if budget > _allowance(value.name):
            over_budget.append(
                f"{value.name}: {entry_bytes / 1e6:.2f} MB/entry x {value.maxsize} "
                f"= {budget / 1e6:.0f} MB"
            )

    assert measured, "populated no caches — has the request shape changed?"
    assert not over_budget, (
        "these caches exceed their per-cache byte budget "
        f"(default {caches_module.MAX_CACHE_BYTES_PER_CACHE / 1e6:.0f} MB): "
        f"{over_budget}. Lower the maxsize — a ceiling on entry COUNT is not a "
        "ceiling on memory."
    )


def test_the_turn_cache_ceiling_matches_what_a_turn_entry_now_costs():
    """M178. `_turn_path_cache` sat at 14 for a reason that stopped being
    true: it was derived when a turn entry was 11.01 MB (M170, a chained
    three-street solve). M173 replaced that with a standalone one-street
    solve and the entry is now ~0.22 MB, so 14 entries used 3 MB of a
    168 MB budget while turn requests missed the cache.

    The ceiling was never WRONG — it was STALE, sized against an entry the
    product no longer produces. `test_cache_ceilings_are_sized_against_
    what_an_entry_actually_costs` catches a ceiling that is too HIGH; it
    cannot catch one left far too low, because nothing overruns.

    This asserts the other direction: the ceiling must be within reach of
    what the budget actually affords for a real entry. It fails loudly if
    the entry grows (a wider cap, a wider tree) without the ceiling being
    re-derived, and equally if the entry shrinks again and nobody notices.
    """
    from api import caches as caches_module
    from api import main as api_main

    caches_module._SolveCache.clear_all()
    body = {"stack_bb": 100.0, "preflop_action_path": ["raise", "call_or_check"],
            "players": 2, "hero_cards": "AsKs", "board": "3d7s2c",
            "flop_action_path": ["call_or_check", "call_or_check"], "turn_card": "Kd"}
    with TestClient(api_main.app) as client:
        response = client.post("/advise", json=body)
        assert response.status_code == 200, response.json()

    cache = caches_module._turn_path_cache
    assert cache.entries, "the turn request populated no turn cache entry"
    entry_bytes = _deep_size(next(iter(cache.entries.values())))
    affordable = caches_module.MAX_CACHE_BYTES_PER_CACHE // entry_bytes

    assert cache.maxsize * entry_bytes <= caches_module.MAX_CACHE_BYTES_PER_CACHE, (
        f"turn_path: {entry_bytes / 1e6:.2f} MB/entry x {cache.maxsize} exceeds budget")
    # And it must not be left absurdly below what the budget affords —
    # the staleness this milestone fixed. A tenth is generous latitude.
    assert cache.maxsize >= affordable // 10, (
        f"turn_path holds {cache.maxsize} entries where the byte budget affords "
        f"{affordable} at {entry_bytes / 1e6:.2f} MB each — the ceiling looks stale "
        "against what an entry now costs, which is how it sat at 14 after M173 made "
        "entries 50x smaller")


def test_the_multiway_preflop_ceiling_is_derived_from_its_worst_entry():
    """M215. The one cache whose real cost a test cannot afford to measure.

    `_multiway_cache` holds the multiway preflop solve, and its entries
    span 127x — 2.02 MB at 3-max, 40.17 MB at 6-max, 256.56 MB at 9-max
    (all after `prune_empty_nodes`). The ceiling sweep populates 3-max,
    because a 9-max solve costs ~525s, so the number that actually sizes
    this ceiling is RECORDED in `api/caches.py`, not measured live.

    This asserts the derivation still holds, so raising the ceiling
    without re-measuring the entry fails here rather than in production.

    Every earlier figure for this cache came from the wrong entry: M127
    recorded 2.45 MB and M214 recorded 83.589 MB, which are the 3-max and
    6-max entries of a cache whose worst is 632 MB unpruned.
    """
    from api import caches as caches_module
    from api import config as api_config

    budget = (caches_module._multiway_cache.maxsize
              * caches_module.MULTIWAY_PREFLOP_WORST_MB)
    assert budget <= caches_module.MULTIWAY_PREFLOP_DECLARED_BUDGET_MB, (
        f"_multiway_cache holds {caches_module._multiway_cache.maxsize} entries "
        f"of up to {caches_module.MULTIWAY_PREFLOP_WORST_MB} MB = {budget:.0f} MB, "
        f"past its declared "
        f"{caches_module.MULTIWAY_PREFLOP_DECLARED_BUDGET_MB} MB. Re-measure the "
        f"worst entry before raising the ceiling — the 9-max entry is the one "
        f"that matters and no sweep can afford to build it."
    )

    designed = len(api_config.MULTIWAY_PREWARM_STACK_DEPTHS) * 3
    assert caches_module._multiway_cache.maxsize >= designed, (
        f"ceiling {caches_module._multiway_cache.maxsize} is below the "
        f"{designed} entries the prewarm creates, so warmed solves would be "
        f"evicted by their own warm-up")


def test_the_multiway_preflop_entry_is_pruned_before_it_is_cached():
    """M215. That the saving is actually being taken.

    `prune_empty_nodes` removes node_data entries that accumulated
    nothing — 70.6% of them at 6-max — and the ceiling above is derived
    from the PRUNED size. If the prune stopped being applied the entries
    would quietly return to ~2.5x their recorded size, and every ceiling
    derived from a pruned figure would be wrong by that factor with
    nothing failing.

    Uses 3-max, the only multiway preflop solve a test can afford.
    """
    from api import caches as caches_module
    from api import solving as solving_module

    caches_module._SolveCache.clear_all()
    result = solving_module._get_or_solve_multiway(100.0, 3)

    empty = [key for key, table in result.node_data.items()
             if not table.strategy_sum.any() and not table.regret_sum.any()]
    assert not empty, (
        f"{len(empty)} of {len(result.node_data)} node_data entries in a cached "
        f"multiway preflop result accumulated nothing — prune_empty_nodes is not "
        f"being applied where this cache is filled, and every ceiling derived "
        f"from a pruned entry size is now wrong")
