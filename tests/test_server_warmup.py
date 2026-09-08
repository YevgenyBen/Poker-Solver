"""The warm-up a benchmark has to do because TestClient will not.

M252. A wide benchmark run without this recorded a 72.6-second preflop
request and five more above five seconds — every one a first-touch
multiway solve at a depth production prewarms. The number was real and
the conclusion would have been false: a deployed server pays that once
at boot, in a background thread, and no user waits for it.

The solves themselves are minutes long, so these tests stub the solver
and check the contract: which spots get warmed, and that the timings
come back so a caller can tell a real warm-up from a no-op.
"""
from unittest import mock

import pytest

from bench.server_warmup import warm_multiway


def test_it_warms_every_depth_the_server_prewarms():
    """The default has to track the app's own list, not a copy of it.

    A benchmark warming a different set from `_prewarm_common_depths`
    measures a server state that does not exist.
    """
    from api import config as cfg

    with mock.patch("api.solving._get_or_solve_multiway") as solve:
        timings = warm_multiway(table_sizes=(3, 6), verbose=False)

    warmed = {(call.args[0], call.args[1]) for call in solve.call_args_list}
    expected = {(depth, players)
                for players in (3, 6)
                for depth in cfg.MULTIWAY_PREWARM_STACK_DEPTHS}
    assert warmed == expected, (
        "the warm-up and the server's own prewarm cover different spots"
    )
    assert len(timings) == len(expected)


def test_it_reports_how_long_each_solve_took():
    """A warm-up that silently did nothing looks identical to one that
    worked, and the run downstream would report cold solves as latency.

    Returning the timings is what lets a caller notice: all-zero means
    the caches were already full.
    """
    with mock.patch("api.solving._get_or_solve_multiway"):
        timings = warm_multiway(depths=(100.0, 20.0), table_sizes=(6,),
                                verbose=False)

    assert set(timings) == {"6max_100bb", "6max_20bb"}
    assert all(isinstance(v, float) for v in timings.values())


def test_heads_up_is_not_warmed_by_default():
    """Heads-up runs the exact preflop solver and is not what
    `_prewarm_common_depths` covers — warming it would spend minutes on
    a cost the benchmark is not trying to exclude."""
    with mock.patch("api.solving._get_or_solve_multiway") as solve:
        warm_multiway(verbose=False)
    assert all(call.args[1] != 2 for call in solve.call_args_list)


@pytest.mark.parametrize("depths", [(), None])
def test_an_empty_depth_list_is_not_silently_the_default(depths):
    """`None` means "use the server's list"; an explicitly empty tuple
    means "warm nothing", and the two must not collapse into each other —
    a caller asking for nothing and getting the full prewarm would wait
    minutes for a run it meant to keep cold."""
    with mock.patch("api.solving._get_or_solve_multiway") as solve:
        warm_multiway(depths=depths, table_sizes=(6,), verbose=False)
    if depths == ():
        assert not solve.called
    else:
        assert solve.called
