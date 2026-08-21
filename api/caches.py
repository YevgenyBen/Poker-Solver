"""Per-endpoint solve caches (M61, audit recommendation #4).

Split out of `main.py` alongside `config.py`. Holds the `_SolveCache`
class (M60) and the one instance each endpoint owns — nothing else, so
that "how many caches exist and what are they called" is answerable by
reading one short file rather than grepping a 3,400-line module.

See `_SolveCache`'s own docstring for why it deliberately exposes
`.entries`/`.lock` instead of a `get`/`set` API: the locking disciplines
across call sites genuinely differ and that difference is load-bearing.
"""

import threading

# M58: there is exactly ONE preflop solve cache, _preflop_raw_cache
# below. A second, formatted-response cache (`_cache`) used to sit here
# and independently re-solve the identical spot for GET /solve — see
# CLAUDE.md's M58 entry and docs/project-audit-2026-08-21.md's SS2.1 for
# the measured ~3.2s that wasted on the most common first user journey.
class _SolveCache:
    """One endpoint's solve cache: the dict plus the lock guarding it,
    bundled (M60, audit recommendation #3).

    Two problems this fixes, both real rather than theoretical:

    1. **Adding a cache used to mean remembering two separate places to
       clear it** — `tests/test_api.py`'s autouse fixture listed 13 of
       them by hand, twice (setup AND teardown), and every endpoint
       milestone had to patch both lists. A cache registers ITSELF here
       on construction, so `clear_all()` cannot miss one. Forgetting is
       now structurally impossible rather than merely discouraged.
    2. **A dict and its lock were two independent globals** that
       convention alone kept paired — 28 of them. Now one object.

    Deliberately exposes `.entries` and `.lock` rather than forcing every
    caller through `get`/`set`: the locking DISCIPLINES here genuinely
    differ and that difference is load-bearing, not drift. Most helpers
    hold the lock only around the dict access and solve unlocked (two
    concurrent misses may both solve — an accepted, documented tradeoff);
    `_query_flop`/`_query_flop_from_path` hold it across the WHOLE
    `query_strategy` call, because that primitive has no concurrency
    control of its own (M22); `_query_turn_multiway_from_path` also
    guards `ensure_mccfr_chance_branch`'s in-place `chance_data`
    mutation (M44). Collapsing those into one `get`/`set` API would
    quietly change three endpoints' concurrency behavior — the same
    "unify things that only look alike" trap M50/M59 already had to
    resist. This class owns STORAGE and REGISTRATION; each call site
    keeps owning its own locking policy.
    """

    _registry: list["_SolveCache"] = []

    def __init__(self, name: str):
        self.name = name
        self.entries: dict = {}
        self.lock = threading.Lock()
        _SolveCache._registry.append(self)

    def __len__(self) -> int:
        return len(self.entries)

    def clear(self) -> None:
        with self.lock:
            self.entries.clear()

    @classmethod
    def clear_all(cls) -> None:
        """Every registered cache. Used by tests between cases; safe to
        call at any time (each cache takes its own lock)."""
        for cache in cls._registry:
            cache.clear()

    @classmethod
    def registered(cls) -> list["_SolveCache"]:
        return list(cls._registry)


_multiway_cache = _SolveCache("multiway")
_flop_cache = _SolveCache("flop")
# Deliberately separate from _flop_cache and from each other, not one
# shared dict — the cache key (board, pot, stack_bb, iterations) omits
# max_raises/raise_sizes/the demo pool because those are fixed constants
# per endpoint, not request-varying, which is only safe *because* each
# endpoint has its own dict. A shared dict would let an identical key
# collide between two endpoints with different max_raises.
_flop_turn_cache = _SolveCache("flop_turn")
_flop_to_river_cache = _SolveCache("flop_to_river")
# /solve_flop_multiway's and /solve_flop_turn_multiway's own dicts (M37)
# — same "each endpoint gets its own" reasoning as the pair above; a
# shared dict would let an identical (board, pot, stack_bb, iterations)
# key collide between the two endpoints despite their different
# max_raises/chance-dispatch behavior.
_flop_multiway_cache = _SolveCache("flop_multiway")
_flop_turn_multiway_cache = _SolveCache("flop_turn_multiway")
# /solve_flop_to_river_multiway's own dict (M40) — same "each endpoint
# gets its own" reasoning as every dict above.
_flop_to_river_multiway_cache = _SolveCache("flop_to_river_multiway")
# Not "_flop_query_cache" — this dict IS query_strategy's own `library`
# parameter (poker_solver/library.py), held at module scope across
# requests, a different granularity than the four dicts above (which
# each cache one formatted response/StrategyResult, not a canonical-
# key -> LibraryEntry mapping). _flop_query_lock is held for query_
# strategy's ENTIRE call in _query_flop below, not just around a dict
# read/write the way the four helpers above do — see _query_flop's own
# docstring for why that's a deliberate, stricter departure.
# M60: these last two are NOT endpoint response caches like the ones
# above — they are the `library` dict poker_solver.library.query_strategy
# itself owns and mutates (its own documented contract is a plain dict).
# They live in a _SolveCache purely for the registry/locking bundle; every
# call site hands the engine `.entries`, never the wrapper. Making
# _SolveCache masquerade as a dict instead would be exactly the implicit
# coupling that made this distinction easy to miss in the first place —
# it surfaced here as a real AttributeError during this milestone, not as
# a design review note.
_flop_query_library = _SolveCache("flop_query_library")

# /solve_flop_multiway_from_path's (M42) own plain dict cache —
# deliberately not a partitioned "one library dict per situation" the
# way _path_query_libraries is for /solve_flop_from_path: this endpoint
# doesn't go through query_strategy/query_strategy_from_path at all (both
# are 2-position machinery — solve_flop_multiway is called directly
# instead), so there's no canonical-library collision risk to partition
# against. Keyed on everything the derived situation and the solve
# actually depend on: the action path, players, stack_bb, board, the
# preflop-leg iterations, and flop_iterations — two different requests
# that happen to derive an identical range/pot/stack still get correctly
# separate cache entries if either iteration count differs.
_flop_multiway_path_cache = _SolveCache("flop_multiway_path")

# /solve_turn_multiway_from_path's (M44) own plain dict cache — same
# "no canonical library, keyed on everything the solve depends on"
# reasoning as _flop_multiway_path_cache above. Keyed only on the
# PREFLOP action path (not flop_action_path/turn_card, resolved by
# walking the already-solved tree afterward — the same "resolving is
# free, re-solving isn't" reasoning _turn_path_cache's own M26 key
# already established), plus players/stack_bb/board/the preflop-leg
# iterations/flop_iterations. The lock also guards ensure_flop_turn_
# multiway_branch's own on-demand-build-and-cache call (see
# _query_turn_multiway_from_path below) — that call mutates a cached
# StrategyResult's own chance_data dict in place, so it needs the same
# protection the cache dict's own reads/writes already get.
_turn_multiway_path_cache = _SolveCache("turn_multiway_path")

# M67: MultiwayEquityCache instances, shared across every multiway solve
# that uses the same hand pool — keyed by the pool, NOT by (stack,
# players) the way _multiway_cache is.
#
# The point is that preflop equity is a property of the HANDS alone. It
# does not depend on stack depth (no board, no betting — just "how often
# does this hand beat these hands"), and it does not depend on table size
# beyond what is already encoded in the opponent tuple's own length,
# which is part of MultiwayEquityCache's own key. So a fresh cache per
# spot — which is what _get_or_solve_multiway used to build — threw away
# every simulated equity the moment a different stack depth was
# requested, then recomputed it identically. With PREWARM_STACK_DEPTHS
# holding 7 depths, that is the same work done 7 times.
#
# Keyed by the pool rather than held as a single module-level instance
# for a specific reason: tests/test_api.py's autouse fixture
# monkeypatches MULTIWAY_PREFLOP_HANDS to a small pool AFTER import, and
# an instance built at import time would hold the full 169-class pool
# while the solve ran over 8 — a length mismatch between the equity
# vector and the hand list, i.e. a silent correctness bug rather than a
# slow test. Keying by the pool makes the two impossible to desync.
_multiway_equity_caches = _SolveCache("multiway_equity")

_preflop_raw_cache = _SolveCache("preflop_raw")
# Deliberately NOT one shared dict like _flop_query_library above — see
# the module docstring's Finding 2. This endpoint's range/pot are
# derived fresh per request from each client's own action_path, unlike
# /solve_flop_cached's fixed demo range, so a shared canonical (board,
# stack) key could silently serve one real situation's answer to an
# unrelated one. One private library per distinct (action_path,
# stack_bb, iterations) instead.
_path_query_libraries = _SolveCache("path_query_libraries")

# M26's own plain-dict cache for solve_flop_turn results, deliberately
# separate from every dict above. Keyed narrowly — only what solve_
# flop_turn's own cost actually depends on (preflop_action_path,
# stack_bb, its own iterations, board, turn_iterations) — deliberately
# NOT flop_action_path/turn_card, which are resolved by walking the
# already-solved tree afterward (~0.04ms, measured) rather than by
# re-solving; including them in the key would force a full re-solve per
# distinct turn-card query against an identical situation, defeating
# this endpoint's whole point. Locking mirrors _get_or_solve_flop_
# turn's own looser discipline (around the dict access only, not the
# whole solve) — not query_strategy's atomic whole-call lock, since
# this isn't going through that primitive.
_turn_path_cache = _SolveCache("turn_path")

# M46's own plain-dict cache for solve_flop_to_river results — same
# shape/reasoning as _turn_path_cache above (keyed on what the solve
# itself depends on: preflop_action_path, stack_bb, its own iterations,
# board, river_iterations; deliberately NOT flop_action_path/turn_card/
# turn_action_path/river_card, resolved by walking the already-solved
# tree afterward instead of re-solving).
_river_path_cache = _SolveCache("river_path")
