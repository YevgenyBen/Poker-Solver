"""FastAPI app exposing the preflop solver over HTTP.

GET /solve/{stack_bb} is the primary route: pick an effective stack
depth (in big blinds) and get back BTN's opening-range strategy.

Solves are cached in-process, keyed by (rounded stack_bb, iterations) —
stack depth is expected to come from a discretized UI control (a slider
snapped to whole/5bb increments), so rounding to the nearest bb makes
the cache actually effective across requests. A handful of common depths
are pre-warmed in a background thread on startup so the common case is
instant even for the first real request; the first-ever call on a fresh
machine still has to pay for building the underlying preflop equity
table once (see poker_solver/equity.py) — that happens transparently
the first time it's needed, whether that's a pre-warm or a live request.
"""

import logging
import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Path, Query
from starlette.concurrency import run_in_threadpool

from poker_solver.solver import DEFAULT_ITERATIONS, solve_preflop
from poker_solver.strategy_format import format_solve_response

from .schemas import SolveResponse

logger = logging.getLogger("poker_solver.api")

PREWARM_STACK_DEPTHS = (20, 40, 50, 75, 100, 150, 200)
MAX_ITERATIONS = 20_000

_cache: dict = {}
_cache_lock = threading.Lock()


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


def _prewarm_common_depths() -> None:
    for depth in PREWARM_STACK_DEPTHS:
        try:
            logger.info("pre-warming solve for stack_bb=%s", depth)
            _get_or_solve(depth, DEFAULT_ITERATIONS)
        except Exception:
            logger.exception("pre-warm failed for stack_bb=%s", depth)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if _prewarm_enabled():
        threading.Thread(target=_prewarm_common_depths, daemon=True).start()
    yield


app = FastAPI(title="Poker Solver API", lifespan=lifespan)


@app.get("/solve/{stack_bb}", response_model=SolveResponse)
async def solve(
    stack_bb: float = Path(..., gt=0, description="Effective stack depth, in big blinds"),
    iterations: int = Query(DEFAULT_ITERATIONS, gt=0, le=MAX_ITERATIONS),
):
    try:
        return await run_in_threadpool(_get_or_solve, stack_bb, iterations)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
