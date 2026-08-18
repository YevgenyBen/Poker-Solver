"""FastAPI app exposing the preflop solver over HTTP.

GET /solve/{stack_bb} is the primary route. `players` (2 or 3, default 2)
picks heads-up or 3-max; `position` (default: first-to-act) picks whose
strategy comes back — see the two solving paths below.

Heads-up (players=2) solves are cached in-process, keyed by (rounded
stack_bb, iterations) — stack depth is expected to come from a
discretized UI control (a slider snapped to whole/5bb increments), so
rounding to the nearest bb makes the cache actually effective across
requests. A handful of common depths are pre-warmed in a background
thread on startup so the common case is instant even for the first real
request; the first-ever call on a fresh machine still has to pay for
building the underlying preflop equity table once (see
poker_solver/equity.py) — that happens transparently the first time it's
needed, whether that's a pre-warm or a live request.

3-max (players=3) solves use MCCFR over a small curated hand subset, not
the full 169 classes — a real 169-hand 3-max MCCFR solve was measured
during M8 to take well over 10 minutes even at a modest iteration count
(the lazy per-matchup equity cache has to pay for a great many distinct
opponent-hand combinations at that scale), which isn't viable for an
interactive endpoint. The curated subset (DEMO_MULTIWAY_HANDS — the same
one test_solver.py's 3-max tests use, so its convergence behavior is
already validated there) keeps this fast enough to serve live. This is a
real, documented v1 scope limit, not a hidden shortcut: it demonstrates
the N-player-general engine (M8's actual deliverable), not a production-
grade 3-max range chart — see the project plan's M9 for scaling this up.
"""

import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path as FilePath

from fastapi import FastAPI, HTTPException, Path, Query
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from poker_solver.equity import MultiwayEquityCache
from poker_solver.game_tree import GameConfig
from poker_solver.solver import DEFAULT_ITERATIONS, StrategyResult, solve_preflop
from poker_solver.starting_hands import StartingHand
from poker_solver.strategy_format import format_solve_response

from .schemas import SolveResponse

# The React app's production build (see frontend/, `npm run build`). Not
# committed to git — build it locally or in CI before serving for real.
FRONTEND_DIST_DIR = FilePath(__file__).resolve().parent.parent / "frontend" / "dist"

logger = logging.getLogger("poker_solver.api")

PREWARM_STACK_DEPTHS = (20, 40, 50, 75, 100, 150, 200)
MAX_ITERATIONS = 20_000

# Same 8-hand pool as tests/test_solver.py's three_max_result fixture —
# see that file's comment for why it's deliberately NOT pair-heavy (an
# earlier version was, and it made "premium hands rarely fold" a false
# expectation even for correctly-solved hands). Kept identical here so
# this endpoint's behavior is covered by that test's convergence checks,
# not just independently hoped to work.
MULTIWAY_POSITIONS = ("BTN", "SB", "BB")
DEMO_MULTIWAY_HANDS = [
    StartingHand("A", "A"),
    StartingHand("K", "K"),
    StartingHand("A", "K", suited=True),
    StartingHand("Q", "Q"),
    StartingHand("A", "K", suited=False),
    StartingHand("T", "9", suited=False),
    StartingHand("7", "2", suited=False),
    StartingHand("3", "2", suited=False),
]
DEMO_MULTIWAY_ITERATIONS = 100_000

_cache: dict = {}
_cache_lock = threading.Lock()
_multiway_cache: dict = {}
_multiway_lock = threading.Lock()


def _prewarm_enabled() -> bool:
    """Off switch for tests (and anyone embedding the app) — avoids
    paying for a handful of full solves just to start the app up."""
    return os.environ.get("POKER_SOLVER_PREWARM", "1") != "0"


def _cache_key(stack_bb: float, iterations: int) -> tuple:
    return (round(stack_bb), iterations)


def _get_or_solve(stack_bb: float, iterations: int) -> dict:
    key = _cache_key(stack_bb, iterations)
    with _cache_lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached

    result = solve_preflop(stack_bb=stack_bb, iterations=iterations)
    response = format_solve_response(result)

    with _cache_lock:
        _cache[key] = response
    return response


def _get_or_solve_multiway(stack_bb: float) -> StrategyResult:
    """Solves (or returns the cached result of solving) the full 3-max
    tree once for `stack_bb`, over DEMO_MULTIWAY_HANDS — every position's
    strategy is derived from this single cached StrategyResult, so
    switching `position` in the API/UI never triggers a re-solve."""
    key = round(stack_bb)
    with _multiway_lock:
        cached = _multiway_cache.get(key)
    if cached is not None:
        return cached

    config = GameConfig(positions=MULTIWAY_POSITIONS, stack_bb=stack_bb)
    equity_cache = MultiwayEquityCache(hands=DEMO_MULTIWAY_HANDS, seed=1)
    result = solve_preflop(
        config=config,
        hands=DEMO_MULTIWAY_HANDS,
        equity_cache=equity_cache,
        iterations=DEMO_MULTIWAY_ITERATIONS,
        seed=1,
    )

    with _multiway_lock:
        _multiway_cache[key] = result
    return result


def _prewarm_common_depths() -> None:
    for depth in PREWARM_STACK_DEPTHS:
        try:
            logger.info("pre-warming solve for stack_bb=%s", depth)
            _get_or_solve(depth, DEFAULT_ITERATIONS)
        except Exception:
            logger.exception("pre-warm failed for stack_bb=%s", depth)

    try:
        logger.info("pre-warming 3-max solve for stack_bb=100")
        _get_or_solve_multiway(100.0)
    except Exception:
        logger.exception("pre-warm failed for 3-max stack_bb=100")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if _prewarm_enabled():
        threading.Thread(target=_prewarm_common_depths, daemon=True).start()
    yield


app = FastAPI(title="Poker Solver API", lifespan=lifespan)


@app.get("/solve/{stack_bb}", response_model=SolveResponse)
async def solve(
    stack_bb: float = Path(..., gt=0, description="Effective stack depth, in big blinds"),
    iterations: int = Query(DEFAULT_ITERATIONS, gt=0, le=MAX_ITERATIONS, description="Heads-up only"),
    players: int = Query(2, ge=2, le=3, description="2 (heads-up) or 3 (3-max demo)"),
    position: str | None = Query(None, description="Which position's strategy to return"),
):
    try:
        if players == 2:
            return await run_in_threadpool(_get_or_solve, stack_bb, iterations)

        result = await run_in_threadpool(_get_or_solve_multiway, stack_bb)
        return format_solve_response(result, position=position)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# Registered last so it only catches requests /solve doesn't match —
# Starlette checks routes in registration order, and a Mount only
# matches as a fallback for paths its earlier siblings didn't claim.
# html=True serves frontend/dist/index.html for "/" and other paths
# (client-side routing would need this too, though this app has none).
if FRONTEND_DIST_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST_DIR), html=True), name="frontend")
