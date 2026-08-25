"""Parallel equity-table construction for chance-node branches (M129).

A turn solve builds ~49 equity tables and a river solve ~2,400. Each is a
pure function of `(board, combos)` and independent of every other, so
they are the one part of a postflop solve that parallelises cleanly — the
CFR walk that follows is a sequential tree traversal over shared regret
state and is not a candidate.

This lives in `api/` rather than in the engine on purpose.
`poker_solver/` is a plain library with no runtime infrastructure
(enforced by tests/test_package_boundary.py), and owning a process pool
is a property of whoever owns the request. The engine takes an
`equity_batch_fn` and defaults to sequential.

**Worker count is 8, not 24, and that is measured.** Interleaved against
a sequential arm in the same run, 49 branches:

    workers   4      8      16     24
    speedup   2.57x  3.38x  2.84x  2.34x

It peaks at 8 and *declines* after. Each branch is only tens of
milliseconds, so past that point the pool spends more on dispatch than
the work is worth — and this machine reports 24 logical cores, so the
naive "one worker per core" would have been the worst setting tried.
Determinism is unaffected: same board, same combos, same seed give
bit-identical tables, which was asserted rather than assumed.
"""

import atexit
import os
import threading
from concurrent.futures import ProcessPoolExecutor

# Measured: see the module docstring. Overridable for tests and for hosts
# whose core count makes a different value sensible.
EQUITY_POOL_WORKERS = int(os.environ.get("POKER_SOLVER_EQUITY_WORKERS", "8"))

# Below this many tables the pool costs more than it saves — dispatch and
# result transfer are not free, and a flop node builds exactly one table.
MIN_BATCH_FOR_POOL = 8

_pool: ProcessPoolExecutor | None = None
_pool_lock = threading.Lock()
# Set once a host has been shown unable to spawn workers, so the probe
# below runs at most one time rather than on every request.
_pool_unavailable = False


def _probe(_):
    """Trivial task used to confirm workers can actually start."""
    return True


def _worker(args):
    """Build one branch's table. Module-level and argument-only so it
    pickles; imports happen in the worker rather than at module import so
    a host that never parallelises never pays for them."""
    import random

    import numpy as np

    from poker_solver.board_equity import build_board_equity_table
    from poker_solver.cards import Card
    from poker_solver.combos import HandCombo

    board_tokens, combo_tokens, seed = args
    board = tuple(Card.from_str(t) for t in board_tokens)
    combos = [HandCombo(Card.from_str(a), Card.from_str(b)) for a, b in combo_tokens]
    table = build_board_equity_table(board, combos, rng=random.Random(seed))
    return np.nan_to_num(table, nan=0.5)


def _get_pool() -> ProcessPoolExecutor | None:
    """One pool for the process, created on first real use.

    Lazily, because most requests never need it and a pool costs real
    memory per worker; once, because creating one per request under
    concurrent load is how a server runs out of processes.
    """
    global _pool, _pool_unavailable
    if _pool_unavailable:
        return None
    if _pool is None:
        with _pool_lock:
            if _pool_unavailable:
                return None
            if _pool is None:
                try:
                    pool = ProcessPoolExecutor(max_workers=EQUITY_POOL_WORKERS)
                    # M132: prove a worker can actually START before
                    # handing real work to the pool.
                    #
                    # Construction succeeds on hosts where the workers
                    # then die — Windows `spawn` re-imports `__main__`,
                    # which fails outright when the parent was launched
                    # from stdin (`python - <<EOF`). The fallback below
                    # already kept answers correct there, but every
                    # attempt printed a worker traceback per worker, and
                    # a request that quietly works while emitting eight
                    # stack traces reads exactly like one that is broken.
                    # One cheap probe, once, turns that into a single
                    # decision.
                    list(pool.map(_probe, [0]))
                    _pool = pool
                except Exception:
                    # A host that cannot spawn still works, just
                    # sequentially. Never fail a request over an
                    # optimisation.
                    try:
                        pool.shutdown(wait=False, cancel_futures=True)
                    except Exception:
                        pass
                    _pool_unavailable = True
                    return None
    return _pool


def shutdown() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.shutdown(wait=False, cancel_futures=True)
            _pool = None


atexit.register(shutdown)


def parallel_equity_batch(boards, combos):
    """`equity_batch_fn` for `chance.build_chance_node`.

    Returns one `nan_to_num`'d table per board, in the order given.
    Falls back to sequential on a small batch, on a host that cannot
    spawn, or if the pool raises — the answer must never depend on
    whether parallelism was available.
    """
    import random

    import numpy as np

    from poker_solver.board_equity import build_board_equity_table

    def _sequential():
        return [np.nan_to_num(build_board_equity_table(b, combos), nan=0.5)
                for b in boards]

    if len(boards) < MIN_BATCH_FOR_POOL:
        return _sequential()

    pool = _get_pool()
    if pool is None:
        return _sequential()

    combo_tokens = [(str(c.card_a), str(c.card_b)) for c in combos]
    # `build_board_equity_table`'s own default seed, so a parallel build
    # and a sequential one produce the SAME table rather than merely
    # equivalent ones.
    from poker_solver.board_equity import DEFAULT_SEED

    tasks = [(tuple(str(card) for card in b), combo_tokens, DEFAULT_SEED) for b in boards]
    try:
        return list(pool.map(_worker, tasks, chunksize=2))
    except Exception:
        shutdown()
        return _sequential()


# --------------------------------------------------------------------
# M132: splitting ONE table across workers.
#
# `parallel_equity_batch` above maps over BOARDS, which is what a turn or
# river solve needs — it builds ~49 or ~2,400 separate tables. A flop
# solve builds exactly one, so that mapper never helped it, and after
# M131 widened the range the flop became the slowest street in the
# product at ~11s median.
#
# One table is still embarrassingly parallel, just along a different
# axis: row `a_pos` of the upper triangle owns the pairs
# (a_pos, a_pos+1..n), disjoint from every other row. M132 made
# `build_board_equity_table` seed per row so a slice is bit-identical to
# the same rows of a full build, which is what lets this merge exactly
# rather than approximately.
# --------------------------------------------------------------------

# Below this many combos the split costs more than it saves.
MIN_COMBOS_FOR_SPLIT = 120


def _row_worker(args):
    import random

    import numpy as np

    from poker_solver.board_equity import build_board_equity_table
    from poker_solver.cards import Card
    from poker_solver.combos import HandCombo

    board_tokens, combo_tokens, samples, seed, rows = args
    board = tuple(Card.from_str(t) for t in board_tokens)
    combos = [HandCombo(Card.from_str(a), Card.from_str(b)) for a, b in combo_tokens]
    return build_board_equity_table(board, combos, samples=samples,
                                    rng=random.Random(seed), pair_rows=rows)


def _balanced_row_bands(n_rows, n_bands):
    """Split rows so each band holds a similar number of PAIRS.

    Row `a_pos` owns `n - a_pos - 1` pairs, so equal row counts would
    give the first worker most of the work and the last almost none.
    Bands are cut at even quantiles of cumulative pair count instead.
    """
    total = n_rows * (n_rows - 1) // 2
    if total <= 0 or n_bands <= 1:
        return [(0, n_rows)]
    bands, start, done, target = [], 0, 0, total / n_bands
    for row in range(n_rows):
        done += n_rows - row - 1
        if done >= target * (len(bands) + 1) and len(bands) < n_bands - 1:
            bands.append((start, row + 1))
            start = row + 1
    bands.append((start, n_rows))
    return [b for b in bands if b[0] < b[1]]


def parallel_board_equity_table(board, combos, samples=None):
    """Build one equity table across the pool, merging row bands.

    Falls back to a plain single-process build on a small pool, a host
    that cannot spawn, or any failure — the table must not depend on
    whether parallelism was available.
    """
    import random

    import numpy as np

    from poker_solver.board_equity import (DEFAULT_BOARD_EQUITY_SAMPLES,
                                           DEFAULT_SEED, build_board_equity_table)

    actual_samples = DEFAULT_BOARD_EQUITY_SAMPLES if samples is None else samples

    def _sequential():
        return build_board_equity_table(board, combos, samples=actual_samples,
                                        rng=random.Random(DEFAULT_SEED))

    if len(combos) < MIN_COMBOS_FOR_SPLIT:
        return _sequential()
    pool = _get_pool()
    if pool is None:
        return _sequential()

    # Bands are cut over the VALID rows, which is what the engine indexes
    # `pair_rows` against — combos blocked by the board are skipped there.
    board_set = frozenset(board)
    n_valid = sum(1 for c in combos if not c.blocks(board_set))
    bands = _balanced_row_bands(n_valid, EQUITY_POOL_WORKERS)
    if len(bands) <= 1:
        return _sequential()

    combo_tokens = [(str(c.card_a), str(c.card_b)) for c in combos]
    board_tokens = tuple(str(c) for c in board)
    tasks = [(board_tokens, combo_tokens, actual_samples, DEFAULT_SEED, b) for b in bands]
    try:
        parts = list(pool.map(_row_worker, tasks))
    except Exception:
        shutdown()
        return _sequential()

    merged = np.full((len(combos), len(combos)), np.nan, dtype=float)
    for part in parts:
        filled = np.isfinite(part)
        merged[filled] = part[filled]
    return merged
