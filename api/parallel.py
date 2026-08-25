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
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                try:
                    _pool = ProcessPoolExecutor(max_workers=EQUITY_POOL_WORKERS)
                except (OSError, ValueError):
                    # A host that cannot spawn processes still works, just
                    # sequentially. Never fail a request over an optimisation.
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
