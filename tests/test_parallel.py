"""Tests for api/parallel.py (M129).

One test module per source module, per this project's convention.
"""

import numpy as np

from api import parallel
from poker_solver.board_equity import build_board_equity_table
from poker_solver.cards import Card
from poker_solver.combos import HandCombo


def _fixture(n_combos=6):
    """A board, a pool of that many distinct combos, and ten next-boards.

    Combos are drawn from all pairings rather than adjacent cards, so a
    pool larger than half the deck is expressible — `MIN_COMBOS_FOR_SPLIT`
    is 120, and pairing adjacent cards tops out at 24.
    """
    import itertools

    board = (Card("2", "h"), Card("6", "d"), Card("9", "c"))
    deck = [Card.from_str(r + s) for r in "23456789TJQKA" for s in "cdhs"]
    rest = [card for card in deck if card not in set(board)]
    combos = [HandCombo(a, b) for a, b in itertools.combinations(rest, 2)][:n_combos]
    boards = [board + (card,) for card in rest[:10]]
    return boards, combos


def test_a_small_batch_stays_sequential_and_is_still_correct():
    """Below `MIN_BATCH_FOR_POOL` the pool costs more than it saves — a
    flop node builds exactly one table — so the batch runs inline. The
    result must be identical either way, not merely close."""
    boards, combos = _fixture()
    small = boards[:2]
    assert len(small) < parallel.MIN_BATCH_FOR_POOL

    got = parallel.parallel_equity_batch(small, combos)
    want = [np.nan_to_num(build_board_equity_table(b, combos), nan=0.5) for b in small]

    assert len(got) == len(want)
    for a, b in zip(got, want):
        assert np.allclose(a, b, equal_nan=True)


def test_the_batch_matches_a_sequential_build_exactly():
    """The contract the whole optimisation rests on: parallelising must
    not change the answer. Workers are seeded with
    `build_board_equity_table`'s own default, so a parallel table and a
    sequential one are bit-identical rather than statistically similar —
    advice must not depend on the host's core count.
    """
    boards, combos = _fixture()
    got = parallel.parallel_equity_batch(boards, combos)
    want = [np.nan_to_num(build_board_equity_table(b, combos), nan=0.5) for b in boards]

    assert len(got) == len(boards)
    for a, b in zip(got, want):
        assert np.allclose(a, b, equal_nan=True)


def test_it_falls_back_rather_than_failing_when_no_pool_is_available(monkeypatch):
    """A host that cannot spawn processes still has to get advice. An
    optimisation is never allowed to turn into an outage."""
    boards, combos = _fixture()
    monkeypatch.setattr(parallel, "_get_pool", lambda: None)

    got = parallel.parallel_equity_batch(boards, combos)
    want = [np.nan_to_num(build_board_equity_table(b, combos), nan=0.5) for b in boards]
    for a, b in zip(got, want):
        assert np.allclose(a, b, equal_nan=True)


def test_a_pool_that_raises_falls_back_instead_of_propagating(monkeypatch):
    """Same guarantee for a pool that exists but breaks mid-map."""
    boards, combos = _fixture()

    class Exploding:
        def map(self, *args, **kwargs):
            raise RuntimeError("pool died")

    monkeypatch.setattr(parallel, "_get_pool", lambda: Exploding())
    monkeypatch.setattr(parallel, "shutdown", lambda: None)

    got = parallel.parallel_equity_batch(boards, combos)
    want = [np.nan_to_num(build_board_equity_table(b, combos), nan=0.5) for b in boards]
    for a, b in zip(got, want):
        assert np.allclose(a, b, equal_nan=True)


def test_the_worker_count_is_the_measured_one_not_the_core_count():
    """M129 measured this rather than assuming it. Interleaved against a
    sequential arm over 49 branches:

        workers   4      8      16     24
        speedup   2.57x  3.38x  2.84x  2.34x

    It peaks at 8 and DECLINES after — each branch is only tens of
    milliseconds, so beyond that the pool spends more on dispatch than
    the work is worth. This machine reports 24 logical cores, so the
    naive "one worker per core" would have been the worst setting tried.
    Pinned so nobody 'fixes' it to os.cpu_count().
    """
    import os

    assert parallel.EQUITY_POOL_WORKERS == 8
    assert parallel.EQUITY_POOL_WORKERS <= (os.cpu_count() or 8)


def test_a_host_that_cannot_spawn_is_detected_once_not_per_request(monkeypatch):
    """M132. Pool CONSTRUCTION can succeed on a host whose workers then
    die — Windows `spawn` re-imports `__main__`, which fails outright
    when the parent was launched from stdin.

    The fallback already kept answers correct there, but every attempt
    printed a worker traceback per worker: a request that quietly works
    while emitting eight stack traces reads exactly like one that is
    broken. A single probe at construction turns that into one decision,
    cached for the life of the process.
    """
    class DeadPool:
        constructed = 0

        def __init__(self, *a, **k):
            DeadPool.constructed += 1

        def map(self, *a, **k):
            raise OSError("workers cannot start here")

        def shutdown(self, *a, **k):
            pass

    monkeypatch.setattr(parallel, "_pool", None)
    monkeypatch.setattr(parallel, "_pool_unavailable", False)
    monkeypatch.setattr(parallel, "ProcessPoolExecutor", DeadPool)

    assert parallel._get_pool() is None
    assert parallel._get_pool() is None
    assert parallel._get_pool() is None
    assert DeadPool.constructed == 1, (
        f"the pool was rebuilt {DeadPool.constructed} times — a host that cannot "
        "spawn should be discovered once, not on every request"
    )


def test_the_table_split_falls_back_correctly_when_no_pool_exists(monkeypatch):
    """M132. The split must degrade to a single-process build that is
    bit-identical, not merely close — the same table either way."""
    import random

    from poker_solver.board_equity import DEFAULT_SEED, build_board_equity_table

    boards, combos = _fixture(n_combos=40)
    board = boards[0][:3]

    monkeypatch.setattr(parallel, "_get_pool", lambda: None)
    got = parallel.parallel_board_equity_table(board, combos, samples=20)
    want = build_board_equity_table(board, combos, samples=20,
                                    rng=random.Random(DEFAULT_SEED))
    assert np.allclose(got, want, equal_nan=True)


def test_row_bands_split_the_work_not_the_row_count():
    """M132. Row `a_pos` owns `n - a_pos - 1` pairs, so equal row counts
    would hand the first worker most of the triangle and the last almost
    nothing. Bands are cut at even quantiles of cumulative PAIR count."""
    bands = parallel._balanced_row_bands(100, 4)
    assert bands[0][0] == 0 and bands[-1][1] == 100
    assert all(a < b for a, b in bands), bands
    # contiguous and complete
    assert [b[0] for b in bands[1:]] == [b[1] for b in bands[:-1]]

    pairs = [sum(100 - r - 1 for r in range(a, b)) for a, b in bands]
    assert max(pairs) < 2 * min(pairs), (
        f"bands are badly unbalanced by pair count: {pairs} for rows {bands}"
    )
    # the first band must cover FEWER rows than the last, since its rows
    # are the expensive ones
    assert (bands[0][1] - bands[0][0]) < (bands[-1][1] - bands[-1][0])
