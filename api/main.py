"""FastAPI app exposing the preflop solver over HTTP.

GET /solve/{stack_bb} is the primary route. `players` (2, 3, 6, or 9,
default 2) picks the table size; `position` (default: first-to-act)
picks whose strategy comes back — see the two solving paths below.

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

Multiway (players=3/6/9) solves use MCCFR over a small curated hand
subset, not the full 169 classes — a real 169-hand 3-max MCCFR solve was
measured during M8 to take well over 10 minutes even at a modest
iteration count (the lazy per-matchup equity cache has to pay for a
great many distinct opponent-hand combinations at that scale), which
isn't viable for an interactive endpoint. The curated subset
(DEMO_MULTIWAY_HANDS — the same one test_solver.py's multiway tests use,
so convergence behavior is already validated there) keeps this fast
enough to serve live. This is a real, documented scope limit, not a
hidden shortcut: it demonstrates the N-player-general engine, not a
production-grade multiway range chart.

Per-table-size iteration budgets (MULTIWAY_TABLE_CONFIGS) shrink as
player count grows, and that's not just "less time budgeted" — it's a
real, measured tradeoff. M9 found that MultiwayEquityCache's cache-hit
rate (the thing that makes repeated solving fast) collapses as opponent
count grows: the space of possible opponent-hand combinations is
roughly (hand pool size)^(opponent count), so a cache hit at 3-max
(2 opponents) is common but at 9-max (8 opponents) is rare regardless of
how fast any *individual* equity computation is — see
poker_solver/hand_eval.py and equity.py for the vectorized evaluator
that was built to make each computation itself fast, and cfr.py's
EXPLORATION_EPSILON docstring for the sampling-bias fix that makes
higher iteration counts actually converge rather than just take longer.
Net effect: 6-max reaches good convergence in minutes; 9-max is
deliberately budgeted fewer iterations and correspondingly noisier —
documented, not hidden, the same way M8 documented 3-max's own limits.

GET /equity is M10's deliverable: given two concrete hand combos (not
169-class hands — see poker_solver/combos.py for why postflop reasoning
moves to combo granularity) and an optional board (0, 3, 4, or 5 cards),
returns each side's win/tie share. No CFR involved, no caching needed —
a single matchup is fast enough to compute live (poker_solver/
board_equity.py's module-level comment has the measured numbers for why
a *whole range* isn't, which is exactly why this endpoint takes two
hands, not two ranges).

GET /solve_flop is M11's deliverable: a real heads-up (OOP/IP) flop
betting round, board + pot + stack in, hero's per-combo strategy out
(poker_solver/solver.py's solve_flop). Like /equity but unlike /solve's
multiway demo, this can't just reuse a fixed hand *pool* the way
DEMO_MULTIWAY_HANDS does — a flop combo range has to exclude whatever
the board itself blocks, which varies per request. So the curated input
here (DEMO_FLOP_HERO_CLASSES/DEMO_FLOP_VILLAIN_CLASSES) is one level up:
small hand-class sets, expanded into the actual board-legal combo range
per request via combos.range_from_class_frequencies — exactly the
bridge function that module exists for. Measured wall-clock for this
pool size (3 hero classes / 4 villain classes, ~30-ish combos after
expansion): ~2.6s end to end, the large majority of it board_equity's
Monte Carlo table build, not the CFR solve itself (~0.2s) — comfortably
fine for a live request, cached per (board, pot, stack_bb, iterations)
the same way multiway solves are cached per (stack_bb, players).
"""

import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path as FilePath

from fastapi import FastAPI, HTTPException, Path, Query
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from poker_solver.board_equity import two_combo_equity
from poker_solver.cards import parse_cards
from poker_solver.combos import HandCombo, range_from_class_frequencies
from poker_solver.equity import MultiwayEquityCache
from poker_solver.game_tree import GameConfig
from poker_solver.solver import DEFAULT_FLOP_ITERATIONS, DEFAULT_ITERATIONS, StrategyResult, solve_flop, solve_preflop
from poker_solver.starting_hands import StartingHand
from poker_solver.strategy_format import format_flop_response, format_solve_response

from .schemas import EquityResponse, FlopSolveResponse, SolveResponse

# The React app's production build (see frontend/, `npm run build`). Not
# committed to git — build it locally or in CI before serving for real.
FRONTEND_DIST_DIR = FilePath(__file__).resolve().parent.parent / "frontend" / "dist"

logger = logging.getLogger("poker_solver.api")

PREWARM_STACK_DEPTHS = (20, 40, 50, 75, 100, 150, 200)
MAX_ITERATIONS = 20_000

# Same 8-hand pool as tests/test_solver.py's multiway fixtures — see that
# file's comment for why it's deliberately NOT pair-heavy (an earlier
# version was, and it made "premium hands rarely fold" a false
# expectation even for correctly-solved hands). Kept identical here so
# this endpoint's behavior is covered by those tests' convergence
# checks, not just independently hoped to work.
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

# One entry per supported multiway table size. `iterations` shrinks as
# player count grows — see the module docstring for why (cache-hit rate
# collapses with opponent count, not solvable by raw speed alone).
# Measured during M9 (samples=200, this hand pool, 100bb): 3-max reaches
# good convergence in seconds once cached; 6-max in ~2.5 minutes (30K
# iterations); 9-max's per-iteration cost was too variable to safely
# budget a large count for a live endpoint (some iterations touch far
# more distinct opponent-hand combinations than others), so it's capped
# at a smaller, empirically-verified-reliable count (~1.5 minutes) —
# genuinely noisier than 6-max's output, not just "less time given".
MULTIWAY_TABLE_CONFIGS = {
    3: {"positions": ("BTN", "SB", "BB"), "iterations": 100_000},
    6: {"positions": ("UTG", "MP", "CO", "BTN", "SB", "BB"), "iterations": 30_000},
    9: {"positions": ("UTG", "UTG1", "MP1", "MP2", "MP3", "CO", "BTN", "SB", "BB"), "iterations": 300},
}

# /solve_flop's curated demo ranges — small hand-*class* sets (not
# combo lists, unlike DEMO_MULTIWAY_HANDS), expanded into actual
# board-legal combos per request via combos.range_from_class_frequencies
# (see the module docstring above for why: a fixed combo list can't
# account for whatever a given board blocks). Hero's a tight
# value-leaning range, villain's a bit wider including one clearly
# air-ish class (84s) so a request can show a real fold-vs-continue
# spread, not just "everything strong". Kept deliberately small — see
# the module docstring for the measured ~2.6s wall-clock this pool size
# costs, dominated by board_equity's table build, not the CFR solve.
MAX_FLOP_ITERATIONS = 20_000

DEMO_FLOP_HERO_CLASSES = {
    StartingHand("A", "A"): 1.0,
    StartingHand("K", "K"): 1.0,
    StartingHand("A", "K", suited=True): 1.0,
}
DEMO_FLOP_VILLAIN_CLASSES = {
    StartingHand("Q", "Q"): 1.0,
    StartingHand("A", "Q", suited=True): 1.0,
    StartingHand("T", "9", suited=True): 1.0,
    StartingHand("8", "4", suited=True): 1.0,
}

_cache: dict = {}
_cache_lock = threading.Lock()
_multiway_cache: dict = {}
_multiway_lock = threading.Lock()
_flop_cache: dict = {}
_flop_lock = threading.Lock()


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


def _get_or_solve_multiway(stack_bb: float, players: int) -> StrategyResult:
    """Solves (or returns the cached result of solving) the full
    `players`-max tree once for `stack_bb`, over DEMO_MULTIWAY_HANDS —
    every position's strategy is derived from this single cached
    StrategyResult, so switching `position` in the API/UI never triggers
    a re-solve."""
    key = (round(stack_bb), players)
    with _multiway_lock:
        cached = _multiway_cache.get(key)
    if cached is not None:
        return cached

    table = MULTIWAY_TABLE_CONFIGS[players]
    config = GameConfig(positions=table["positions"], stack_bb=stack_bb)
    equity_cache = MultiwayEquityCache(hands=DEMO_MULTIWAY_HANDS, seed=1)
    result = solve_preflop(
        config=config,
        hands=DEMO_MULTIWAY_HANDS,
        equity_cache=equity_cache,
        iterations=table["iterations"],
        seed=1,
    )

    with _multiway_lock:
        _multiway_cache[key] = result
    return result


def _get_or_solve_flop(board_cards: tuple, pot: float, stack_bb: float, iterations: int) -> StrategyResult:
    """Solves (or returns the cached result of solving) DEMO_FLOP_HERO/
    VILLAIN_CLASSES' board-legal expansion for one (board, pot, stack_bb,
    iterations) request — cached the same way multiway solves are, so
    switching `position` in the API/UI never triggers a re-solve."""
    key = (board_cards, round(pot, 2), round(stack_bb), iterations)
    with _flop_lock:
        cached = _flop_cache.get(key)
    if cached is not None:
        return cached

    exclude = frozenset(board_cards)
    hero_range = range_from_class_frequencies(DEMO_FLOP_HERO_CLASSES, exclude=exclude)
    villain_range = range_from_class_frequencies(DEMO_FLOP_VILLAIN_CLASSES, exclude=exclude)
    if not hero_range or not villain_range:
        # Only possible with a contrived board (e.g. 3 cards of the same
        # rank blocking a pair class down to nothing) — a real error for
        # the caller, not a crash.
        raise ValueError(f"board {''.join(str(c) for c in board_cards)!r} blocks every demo-range combo")

    result = solve_flop(
        board=board_cards,
        hero_range=hero_range,
        villain_range=villain_range,
        pot=pot,
        effective_stack_bb=stack_bb,
        iterations=iterations,
    )

    with _flop_lock:
        _flop_cache[key] = result
    return result


def _prewarm_common_depths() -> None:
    for depth in PREWARM_STACK_DEPTHS:
        try:
            logger.info("pre-warming solve for stack_bb=%s", depth)
            _get_or_solve(depth, DEFAULT_ITERATIONS)
        except Exception:
            logger.exception("pre-warm failed for stack_bb=%s", depth)

    for players in MULTIWAY_TABLE_CONFIGS:
        try:
            logger.info("pre-warming %s-max solve for stack_bb=100", players)
            _get_or_solve_multiway(100.0, players)
        except Exception:
            logger.exception("pre-warm failed for %s-max stack_bb=100", players)


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
    players: int = Query(2, description="2 (heads-up), or 3/6/9 for a multiway demo"),
    position: str | None = Query(None, description="Which position's strategy to return"),
):
    if players != 2 and players not in MULTIWAY_TABLE_CONFIGS:
        valid = ", ".join(str(p) for p in [2, *MULTIWAY_TABLE_CONFIGS])
        raise HTTPException(status_code=422, detail=f"players must be one of {valid}")
    try:
        if players == 2:
            return await run_in_threadpool(_get_or_solve, stack_bb, iterations)

        result = await run_in_threadpool(_get_or_solve_multiway, stack_bb, players)
        return format_solve_response(result, position=position)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/equity", response_model=EquityResponse)
async def equity(
    hand_a: str = Query(..., description="4-character combo, e.g. AhKh"),
    hand_b: str = Query(..., description="4-character combo, e.g. QsQd"),
    board: str = Query("", description="0, 6, 8, or 10 characters — 0/3/4/5 board cards, e.g. Ts9h2c"),
):
    try:
        combo_a = HandCombo.from_str(hand_a)
        combo_b = HandCombo.from_str(hand_b)
        board_cards = tuple(parse_cards(board))
        equity_a, equity_b = await run_in_threadpool(two_combo_equity, board_cards, combo_a, combo_b)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return EquityResponse(
        hand_a=str(combo_a),
        hand_b=str(combo_b),
        board="".join(str(card) for card in board_cards),
        equity_a=equity_a,
        equity_b=equity_b,
    )


@app.get("/solve_flop", response_model=FlopSolveResponse)
async def solve_flop_endpoint(
    board: str = Query(..., description="Exactly 3 cards, e.g. Jh7d2c"),
    pot: float = Query(10.0, gt=0, description="Pot entering the flop"),
    stack_bb: float = Query(40.0, gt=0, description="Effective stack behind, in big blinds"),
    iterations: int = Query(DEFAULT_FLOP_ITERATIONS, gt=0, le=MAX_FLOP_ITERATIONS),
    position: str | None = Query(None, description="OOP or IP — defaults to OOP, the first to act"),
):
    try:
        board_cards = tuple(parse_cards(board))
        if len(board_cards) != 3:
            raise ValueError(f"board must have exactly 3 cards for a flop, got {len(board_cards)}")
        result = await run_in_threadpool(_get_or_solve_flop, board_cards, pot, stack_bb, iterations)
        return format_flop_response(result, board="".join(str(c) for c in board_cards), position=position)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# Registered last so it only catches requests /solve doesn't match —
# Starlette checks routes in registration order, and a Mount only
# matches as a fallback for paths its earlier siblings didn't claim.
# html=True serves frontend/dist/index.html for "/" and other paths
# (client-side routing would need this too, though this app has none).
if FRONTEND_DIST_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST_DIR), html=True), name="frontend")
