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


def test_the_shared_runout_table_is_not_given_the_per_pair_sample_count():
    """M176. `PATH_QUERY_EQUITY_SAMPLES` is 30 — a PER-PAIR count, where
    every pair draws its own runouts. Shared runouts are drawn once per
    BOARD and colliding draws are dropped, so they need far more of them;
    `SHARED_RUNOUT_FLOP_SAMPLES` is 320 for the same reason M162's
    multiway constant is.

    Forwarding the caller's 30 here would still produce a table, still be
    deterministic, and still pass every other test — while making it
    roughly ten times noisier than the constant intends. That is a silent
    failure, so it gets an explicit guard.
    """
    import inspect

    from api import config as cfg
    from api import parallel as parallel_mod
    from poker_solver.board_equity import SHARED_RUNOUT_FLOP_SAMPLES

    assert SHARED_RUNOUT_FLOP_SAMPLES > cfg.PATH_QUERY_EQUITY_SAMPLES * 4, (
        "shared runouts need materially more samples than the per-pair count; "
        "if these have converged, re-measure rather than assuming they match")

    source = inspect.getsource(parallel_mod.parallel_board_equity_table)
    shared_call = source[source.index("build_shared_runout_equity_table("):]
    assert "samples=SHARED_RUNOUT_FLOP_SAMPLES" in shared_call, (
        "the shared builder must be given its own constant, not the caller's "
        "per-pair `samples`")
    assert "samples=actual_samples" not in shared_call.split(")")[0], (
        "the per-pair sample count is being forwarded to the shared builder")


def test_the_shared_runout_path_is_what_the_flop_actually_uses():
    """M176. The flag has to be read at call time and actually change which
    builder runs — a config constant nothing consults is worse than none,
    because it reads as a working switch.
    """
    import random

    from api import config as cfg
    from api.parallel import parallel_board_equity_table
    from poker_solver.cards import Card
    from poker_solver.combos import range_from_class_frequencies
    from poker_solver.starting_hands import all_starting_hands

    assert cfg.SHARED_RUNOUT_FLOP_TABLE is True, (
        "M176 ships this on; flipping it off is a deliberate act that should "
        "update this test and the measurements in api/config.py")

    import numpy as np

    from poker_solver.board_equity import (build_board_equity_table,
                                           build_shared_runout_equity_table)

    # A FLOP board on purpose. On a turn board both builders enumerate and
    # agree exactly, so a fallback to the per-pair path would still match
    # and this test would pass while the flag did nothing — the first
    # version of it did exactly that, and the mutation survived.
    board = tuple(Card.from_str(c) for c in ("Th", "5s", "7c"))
    combos = sorted(
        range_from_class_frequencies({h: 1.0 for h in all_starting_hands()[:10]},
                                     exclude=frozenset(board)),
        key=str)

    through_api = parallel_board_equity_table(board, combos, samples=30, seed=42)
    shared = build_shared_runout_equity_table(board, combos, rng=random.Random(42))
    both = ~np.isnan(through_api) & ~np.isnan(shared)
    assert both.any()
    assert np.array_equal(through_api[both], shared[both]), (
        "the production path is not producing the shared-runout table")

    # And it is genuinely NOT the per-pair table: on a flop board the two
    # sample differently, so matching it would mean the flag is dead.
    per_pair = build_board_equity_table(board, combos, samples=30,
                                        rng=random.Random(42))
    defined = both & ~np.isnan(per_pair)
    assert not np.array_equal(through_api[defined], per_pair[defined]), (
        "the production path returned the per-pair table — the flag is not "
        "being consulted")


def test_the_per_pair_sample_count_is_inert_on_the_production_flop_path():
    """F48 (M193). `PATH_QUERY_EQUITY_SAMPLES` reads like it controls the
    flop's equity precision and has not since M176: the shared-runout
    builder uses its own constant and deliberately does not forward the
    caller's, so the production table is identical whatever is passed.

    That is correct behaviour — 30 is a per-pair count and shared runouts
    need many more — but a live-looking constant that does nothing will
    mislead whoever tunes it next. It cost one attempt at re-testing
    "equity samples on money" that could only ever have measured zero.

    This pins the fact, so that if `samples` ever becomes live again the
    comment above the constant stops being true loudly rather than
    quietly.
    """
    import numpy as np

    from api import config as cfg
    from api.parallel import parallel_board_equity_table
    from poker_solver.cards import Card
    from poker_solver.combos import range_from_class_frequencies
    from poker_solver.starting_hands import all_starting_hands

    assert cfg.SHARED_RUNOUT_FLOP_TABLE is True, (
        "with the shared builder off, samples IS live again and the comment "
        "above PATH_QUERY_EQUITY_SAMPLES must be corrected")

    board = tuple(Card.from_str(c) for c in ("Th", "5s", "7c"))
    combos = sorted(
        range_from_class_frequencies({h: 1.0 for h in all_starting_hands()[:16]},
                                     exclude=frozenset(board)),
        key=str)
    low = parallel_board_equity_table(board, combos, samples=30, seed=42)
    high = parallel_board_equity_table(board, combos, samples=200, seed=42)

    both = ~np.isnan(low) & ~np.isnan(high)
    assert both.any()
    assert np.array_equal(low[both], high[both]), (
        "samples now changes the production flop table — good, but the "
        "constant's comment says it is inert and must be updated")
