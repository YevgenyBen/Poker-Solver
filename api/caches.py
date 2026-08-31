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
from collections import OrderedDict

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

    def __init__(self, name: str, maxsize: int | None = None):
        self.name = name
        self.maxsize = maxsize
        # M93: an OrderedDict so `store` can evict least-recently-used.
        # Still a plain mapping to every reader, so the many call sites
        # that read `.entries` directly are unaffected.
        self.entries: OrderedDict = OrderedDict()
        # M93: a REENTRANT lock, deliberately. Many call sites hold this
        # lock across a whole read-check-write block and now call `store`
        # / `get` / `setdefault` from inside it — those take the lock
        # themselves, and a plain Lock would self-deadlock. That is not
        # hypothetical: converting the 11 direct `.entries[...] = ...`
        # writes to `store()` hung the entire test suite until this line
        # changed. Reentrancy makes the class safe to call from either
        # side of the lock rather than requiring every caller to know
        # which side it is on.
        #
        # The per-key locks in `_key_locks` stay NON-reentrant on
        # purpose: they gate single-flight, are held across a solve, and
        # are never re-acquired by the thread already holding one.
        # Reentrancy there would silently defeat the gate.
        self.lock = threading.RLock()
        # M92: per-key locks for get_or_compute's single-flight path.
        # Entries are removed once their solve lands, so this never grows
        # to one lock per key ever seen.
        self._key_locks: dict = {}
        _SolveCache._registry.append(self)

    def __len__(self) -> int:
        return len(self.entries)

    def clear(self) -> None:
        with self.lock:
            self.entries.clear()
            self._key_locks.clear()

    def store(self, key, value):
        """Write an entry, evicting the least-recently-used one if this
        cache has a `maxsize` (M93).

        Why a bound exists at all: **nothing here evicted anything**, so
        every entry lived for the life of the process. Measured in round
        12 — heap grew steadily under varied traffic (0.4 MB -> 1.6 MB
        over 25 requests, ~0.065 MB each) with no ceiling. A server
        answering real traffic accumulates a distinct entry per (spot,
        stack, hero class, action path); at that rate 100k requests is
        several GB. A long-running process was going to run out of
        memory, which is the one failure mode a cache is not allowed to
        have.

        Bounded rather than TTL'd because the value here is
        recency-shaped, not age-shaped: a spot being asked about again is
        exactly what makes it worth keeping, and a solve does not go
        stale — the same inputs give the same answer tomorrow.
        """
        with self.lock:
            self._store_locked(key, value)

    def _store_locked(self, key, value):
        """`store`'s body, for callers already holding `self.lock`."""
        if key in self.entries:
            self.entries.move_to_end(key)
        self.entries[key] = value
        if self.maxsize is not None:
            while len(self.entries) > self.maxsize:
                evicted, _ = self.entries.popitem(last=False)
                self._key_locks.pop(evicted, None)

    def get(self, key, default=None):
        """Read an entry AND mark it most-recently-used (M93).

        Reading through `.entries.get(...)` directly does not register a
        hit, so an entry in constant use but never re-stored would age
        out of an LRU as if it were cold — the exact opposite of what the
        policy is for. Call sites that read go through here.
        """
        with self.lock:
            if key not in self.entries:
                return default
            self.entries.move_to_end(key)
            return self.entries[key]

    def setdefault(self, key, default):
        """`dict.setdefault` that participates in the LRU order. Used by
        the path-query libraries, which read-or-create in one step."""
        with self.lock:
            if key in self.entries:
                self.entries.move_to_end(key)
                return self.entries[key]
            self._store_locked(key, default)
            return default

    def touch(self, key):
        """Mark `key` as most-recently-used. Call sites that read
        `.entries` directly (there are several, deliberately — see the
        class docstring) would otherwise never register a hit, so a hot
        entry could be evicted while in constant use."""
        with self.lock:
            if key in self.entries:
                self.entries.move_to_end(key)

    def get_or_compute(self, key, factory):
        """Return `self.entries[key]`, computing it via `factory()` at
        most once even when many threads miss simultaneously (M92).

        The "single-flight" pattern, added because the alternative was
        measured and it is expensive. Every helper here used to check the
        cache under the lock, compute UNLOCKED, then write under the lock
        — safe, and documented as an accepted tradeoff on the grounds
        that these solves are deterministic so either racer's result is
        correct. That reasoning is about CORRECTNESS and it is right. It
        quietly accepted an N-times COST that nobody had measured.

        Measured in M92: **8 concurrent requests sharing one solve key ran
        8 full solves, 223s.** The same 8 sequentially ran 0 (all cache
        hits after the first). A cold spot hit by N simultaneous users
        did N times the work — a thundering herd, and exactly the
        multi-user failure shape M76's cache-key bug also had.

        The lock discipline matters and is easy to get wrong:
          * `self.lock` guards only dict access and the per-key lock
            registry — never a solve, or concurrent misses on DIFFERENT
            keys would serialize against each other for no reason.
          * The per-key lock is held across the solve, so late arrivals
            wait for the winner instead of duplicating it.
          * The cache is re-checked after acquiring the per-key lock,
            since the winner may have finished while we waited.
        """
        with self.lock:
            if key in self.entries:
                self.entries.move_to_end(key)
                return self.entries[key]
            key_lock = self._key_locks.setdefault(key, threading.Lock())

        with key_lock:
            with self.lock:
                if key in self.entries:
                    self.entries.move_to_end(key)
                    return self.entries[key]
            value = factory()
            with self.lock:
                self._store_locked(key, value)
                # The per-key lock has done its job; keeping it would
                # leak one lock object per distinct key forever.
                self._key_locks.pop(key, None)
            return value

    @classmethod
    def clear_all(cls) -> None:
        """Every registered cache. Used by tests between cases; safe to
        call at any time (each cache takes its own lock)."""
        for cache in cls._registry:
            cache.clear()

    @classmethod
    def registered(cls) -> list["_SolveCache"]:
        return list(cls._registry)


# Bounded, but generously — these cost 75-140s each to rebuild and the
# startup pre-warm fills them on purpose, so eviction throws away work
# the server did specifically so a user would not have to wait.
#
# **It used to be unbounded, and that was wrong (M104).** The reasoning
# was "at most a few dozen entries ever exist", which is true of the
# entries the PRE-WARM creates and false of the key, which is
# `(round(stack_bb), players)` — `stack_bb` comes from the request. A
# client walking stack depths mints a new entry per integer depth,
# forever. Measured at **0.152 MB of heap per distinct depth**.
#
# It composes with something judged harmless on its own: M102 measured
# `stack_bb: 1e9` as absurd-but-valid and deliberately left it
# uncapped, because the response is structurally correct and no
# principled ceiling exists. Correct in isolation; combined with an
# unbounded cache keyed on that value it becomes a memory-exhaustion
# path. **Neither finding is a defect alone.**
#
# 64 rather than the 128-256 used below: entries here are far more
# expensive to rebuild, so the ceiling should bind only under genuinely
# adversarial variety, never under the handful of depths a real session
# touches.
# M127. The per-cache byte budget, and the reason it exists.
#
# M93/M104 bounded every cache and M124 re-verified it. That bound is on
# entry COUNT. Entry SIZE was never bounded, and it is not remotely
# uniform: measured, a `preflop_raw` entry is 0.20 MB while a `turn_path`
# entry is 7.59 MB - 38x - yet both were handed ceilings in the same
# 128-256 range, as though entries were interchangeable. `turn_path` at
# 128 was a ~971 MB cache on its own.
#
# Found by PLAYING, not by inspection. A simulated session dealing a
# fresh board every hand grew the working set LINEARLY at 1.4 MB/s with
# no plateau - 1,642 MB to 3,644 MB across 23 minutes, which puts a real
# server past 8 GB inside two hours. Fifteen audit rounds and two
# whole-project diagnostics missed it because none of them played a hand.
#
# 160 MB per cache is the ceiling each `maxsize` is now sized against,
# checked by `test_cache_ceilings_are_sized_against_what_an_entry_
# actually_costs`, which measures a real entry rather than trusting this
# comment. 160 rather than 128 so `multiway` keeps its 64 entries: those
# cost 66-93s each to rebuild, the most expensive solve in the product,
# and 64 x 2.45 MB lands at 157 MB. The point is not the exact figure; it
# is that the process now has a ceiling at all.
#
# Measured per entry (M127), which is what the new ceilings derive from:
#
#     river_path          38.45 MB    turn_path            7.95 MB
#     flop_turn            2.64 MB    multiway             2.45 MB
#     multiway_equity      2.40 MB    flop_multiway_path   0.41 MB
#     preflop_raw          0.21 MB    flop_multiway        0.12 MB
#     path_query_libraries 0.07 MB    flop                 0.06 MB
#     flop_query_library   0.012 MB
#
# Four were over budget and together came to 6.4 GB: river_path at 128
# entries was a **4.9 GB** cache on its own, turn_path 1.0 GB, flop_turn
# 337 MB, multiway 157 MB. Right-sized, every measured cache now fits and
# the combined ceiling is roughly 835 MB.
#
# The deeper fix, deliberately NOT taken here: a river entry is 38 MB
# because it retains the whole flop->river StrategyResult — tree,
# node_data and equity tables — when a caller only ever reads strategies
# off it. Storing less per entry would buy back far more than trimming
# the count does. That is a real change to what the cache holds; this is
# the bound that stops the bleeding.
#
# Shrinking the big caches costs less than it looks like it should. The
# same play session measured the real-world hit rate at ~11% - a session
# never repeats a board, so those large postflop entries were mostly
# being held, not reused.
MAX_CACHE_BYTES_PER_CACHE = 160 * 1024 * 1024


_multiway_cache = _SolveCache("multiway", maxsize=64)
_flop_cache = _SolveCache("flop", maxsize=256)
# Deliberately separate from _flop_cache and from each other, not one
# shared dict — the cache key (board, pot, stack_bb, iterations) omits
# max_raises/raise_sizes/the demo pool because those are fixed constants
# per endpoint, not request-varying, which is only safe *because* each
# endpoint has its own dict. A shared dict would let an identical key
# collide between two endpoints with different max_raises.
# M127: 60, not 128. A flop_turn entry measures 2.64 MB, so 128 was 337 MB.
_flop_turn_cache = _SolveCache("flop_turn", maxsize=60)
_flop_to_river_cache = _SolveCache("flop_to_river", maxsize=128)
# /solve_flop_multiway's and /solve_flop_turn_multiway's own dicts (M37)
# — same "each endpoint gets its own" reasoning as the pair above; a
# shared dict would let an identical (board, pot, stack_bb, iterations)
# key collide between the two endpoints despite their different
# max_raises/chance-dispatch behavior.
_flop_multiway_cache = _SolveCache("flop_multiway", maxsize=128)
_flop_turn_multiway_cache = _SolveCache("flop_turn_multiway", maxsize=128)
# /solve_flop_to_river_multiway's own dict (M40) — same "each endpoint
# gets its own" reasoning as every dict above.
_flop_to_river_multiway_cache = _SolveCache("flop_to_river_multiway", maxsize=128)
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
class _MappingSolveCache(_SolveCache):
    """A bounded cache that engine code can treat as a plain mapping.

    M158. `poker_solver` takes a `warm_store` and uses it as a dict —
    `store.get(key)` and `store[key] = value` — because the engine must
    not know about this module's cache class (the boundary
    tests/test_package_boundary.py enforces). `_SolveCache` already
    supplies `get`; this adds the one missing piece so the same LRU
    bound, eviction and registration apply to it as to every other cache.
    """

    def __setitem__(self, key, value):
        self.store(key, value)

    def __contains__(self, key):
        return self.get(key) is not None


# M158: solved canonical spots, reused to warm-start a later request for
# the same spot with a different hero or a merely suit-isomorphic board.
# Keyed exactly as the canonical library is — (canonical board, canonical
# stack) — so one entry serves every board isomorphic to it.
#
# An entry holds a combo list plus one InfoSetTable per decision node,
# the same order of size as the library entry it accompanies, so it takes
# the library's own ceiling rather than a larger one.
# M172 lowered this from 256. Raising the flop cap 26 -> 100 grew a
# warm-start entry to 1.23 MB (it holds one InfoSetTable per node over
# a ~3x larger combo pool), and 256 of those is 316 MB against a 168 MB
# budget. Caught by the byte-ceiling test, which is the second config
# change in two milestones to trip it — a wider tree is a bigger entry.
_canonical_warm_starts = _MappingSolveCache("canonical_warm_starts", maxsize=128)

# M163: the same idea for the MID-flop node, which cannot use the one
# above. That store is keyed on the CANONICAL board (so one entry serves
# every isomorphic board); this path solves the real board at the real
# stack, and its own `_flop_node_cache` key must include hero (M76). This
# store drops hero and keeps everything that changes the ranges, so the
# second hero to ask about a board refines rather than re-solves.
#
# An entry is the same shape and order of size as a canonical warm start
# — a combo list plus one InfoSetTable per decision node — so it takes
# the same ceiling.
_flop_node_warm_starts = _MappingSolveCache("flop_node_warm_starts", maxsize=256)


_flop_query_library = _SolveCache("flop_query_library", maxsize=2048)

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
_flop_multiway_path_cache = _SolveCache("flop_multiway_path", maxsize=256)

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
_turn_multiway_path_cache = _SolveCache("turn_multiway_path", maxsize=128)

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

# M88: flop solves backing a NON-opening flop decision (see solving.
# _query_flop_node_from_path). Separate from _turn_path_cache because it
# holds a different solve — solve_flop at the canonical library's own
# tree, so both flop decisions model one game (F12) — and separate from
# _flop_query_library because that one stores flattened root strategies
# with no tree to resolve a path into.
_flop_node_cache = _SolveCache("flop_node", maxsize=256)

# Bounded for the same reason as _multiway_cache above (M104): keyed by
# `(round(stack_bb), iterations)`, BOTH of which come from the request,
# so "pre-warmed at startup and cheap to hold" described the pre-warm's
# own entries rather than the cache's real growth. 128 matches the solve
# caches above; a heads-up preflop result is much cheaper to rebuild
# than a multiway one.
_preflop_raw_cache = _SolveCache("preflop_raw", maxsize=128)
# Deliberately NOT one shared dict like _flop_query_library above — see
# the module docstring's Finding 2. This endpoint's range/pot are
# derived fresh per request from each client's own action_path, unlike
# /solve_flop_cached's fixed demo range, so a shared canonical (board,
# stack) key could silently serve one real situation's answer to an
# unrelated one. One private library per distinct (action_path,
# stack_bb, iterations) instead.
_path_query_libraries = _SolveCache("path_query_libraries", maxsize=256)

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
# M178 raised this from 14, because the number it was derived from no
# longer exists.
#
# The history, which is the point: M127 set 20 (from 128) when a turn
# entry measured 7.95 MB, and M170 lowered it to 14 when a sized
# re-raise grew the entry to 11.01 MB — 20 of those being 220 MB against
# a 168 MB budget. Both were correct derivations from a real
# measurement.
#
# **M173 then replaced the chained three-street solve with a standalone
# one-street solve, and nobody re-derived this.** A turn entry now
# measures **0.22 MB** — 50x smaller — so 14 entries occupy 3 MB of a
# 168 MB budget. The ceiling was not wrong, it was STALE: sized against
# an entry the product stopped producing.
#
# 192 is chosen to survive the change most likely to land next. Measured
# entry cost by range cap (M178):
#
#   cap  26 (shipped)  0.22 MB   192 entries =  42 MB
#   cap  60            0.45 MB               =  86 MB
#   cap 100            0.77 MB               = 148 MB   <- still under
#   cap 140            1.07 MB               = 205 MB   <- would FAIL
#
# So adopting a wider turn cap up to 100 keeps this valid, and going to
# 140 forces a deliberate re-derivation rather than a silent overrun —
# which is what `test_cache_ceilings_are_sized_against_what_an_entry_
# actually_costs` is for.
_turn_path_cache = _SolveCache("turn_path", maxsize=192)

# M46's own plain-dict cache for solve_flop_to_river results — same
# shape/reasoning as _turn_path_cache above (keyed on what the solve
# itself depends on: preflop_action_path, stack_bb, its own iterations,
# board, river_iterations; deliberately NOT flop_action_path/turn_card/
# turn_action_path/river_card, resolved by walking the already-solved
# tree afterward instead of re-solving).
# M127: 4, not 128. A river entry measures 38.45 MB — 180x a preflop
# one — so 128 of them was a 4.9 GB cache. See MAX_CACHE_BYTES_PER_CACHE.
# M174: a standalone river entry is ONE street on a complete board and
# measures 0.42 MB, against the chained three-street solve's 38.45 MB
# — 92x smaller. Standalone loses the chained solve's reuse across
# runouts (it keys per board rather than serving every turn/river card
# from one entry), and this is what more than repays it: 256 boards
# held instead of 4, at 107 MB against the 168 MB per-cache budget.
_river_path_cache = _SolveCache("river_path", maxsize=256)
