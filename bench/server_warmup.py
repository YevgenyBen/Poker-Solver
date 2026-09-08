"""Warm the solves a real server has already paid for before it serves.

**`TestClient(app)` does not run the app's lifespan**, so a benchmark
driving the API in-process starts with every cache empty — including the
multiway preflop solves `_prewarm_common_depths` computes in a
background thread at real startup. A benchmark that forgets this
measures cold solves and reports them as latency.

That is not hypothetical. A wide benchmark run without this recorded a
**72.6-second** preflop request and five more above five seconds, every
one of them a first-touch multiway solve at a depth production prewarms
(`MULTIWAY_PREWARM_STACK_DEPTHS` is exactly `(100.0, 50.0, 20.0)`). The
figure is real and it is not a user-facing cost: a deployed server pays
it once at boot, in a thread, before anyone asks.

The existing scratchpad session harness carried its own copy of this
warm-up with a comment explaining the same trap. It lives here now so the
next harness inherits it instead of rediscovering it — and so the reason
is written down somewhere a test can point at.

Cold multiway preflop solves are expensive (M124/M157: ~66s at 6-max,
~93s at 9-max, 500-630s at 9-max in the worst measured case), so warming
takes minutes. That is the cost of measuring the product a user meets
rather than the one a cold process presents.
"""
from __future__ import annotations

import time


def warm_multiway(depths=None, table_sizes=(3, 6), verbose: bool = True) -> dict:
    """Solve, and therefore cache, each (depth, table size) a run will use.

    Returns the wall-clock each one took, which is worth printing: a
    warm-up that reports 0.0s everywhere means the caches were already
    full and the run is not measuring what it thinks it is.

    Heads-up is excluded by default — its exact preflop solve is cheap
    and is not what `_prewarm_common_depths` covers.
    """
    from api import config as cfg
    from api.solving import _get_or_solve_multiway

    if depths is None:
        depths = cfg.MULTIWAY_PREWARM_STACK_DEPTHS

    timings = {}
    for players in sorted(table_sizes):
        for depth in depths:
            started = time.perf_counter()
            _get_or_solve_multiway(depth, players)
            elapsed = time.perf_counter() - started
            timings[f"{players}max_{depth:g}bb"] = round(elapsed, 2)
            if verbose:
                print(f"  warm {players}-max {depth:g}bb: {elapsed:.1f}s",
                      flush=True)
    return timings
