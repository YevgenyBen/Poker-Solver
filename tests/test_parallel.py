"""Tests for api/parallel.py (M129).

One test module per source module, per this project's convention.
"""

import numpy as np

from api import parallel
from poker_solver.board_equity import build_board_equity_table
from poker_solver.cards import Card
from poker_solver.combos import HandCombo


def _fixture(n_combos=6):
    board = (Card("2", "h"), Card("6", "d"), Card("9", "c"))
    deck = [Card.from_str(r + s) for r in "23456789TJQKA" for s in "cdhs"]
    rest = [card for card in deck if card not in set(board)]
    combos = [HandCombo(rest[i], rest[i + 1]) for i in range(0, n_combos * 2, 2)]
    boards = [board + (card,) for card in rest[n_combos * 2: n_combos * 2 + 10]]
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
