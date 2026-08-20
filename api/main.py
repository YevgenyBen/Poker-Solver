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

GET /solve_flop_turn and GET /solve_flop_to_river are M14's deliverable:
the same board/pot/stack-in, hero's-strategy-out shape as /solve_flop,
but backed by solve_flop_turn (M12)/solve_flop_to_river (M13) — a real
turn (and, for the second endpoint, river) betting round chained in via
a real chance node, instead of /solve_flop's "average every remaining
runout" shortcut. Both M12's and M13's own PRs measured /solve_flop's
existing ~33-combo demo pool as far too slow to expose live (183s and
"two-plus hours" respectively) — these two endpoints use a *much*
smaller, separate curated pool instead (DEMO_CHAINED_FLOP_HERO_CLASSES/
DEMO_CHAINED_FLOP_VILLAIN_CLASSES, shared between both endpoints so a
frontend "runout depth" selector compares the same matchup at different
depths, not silently different ranges), sized from real measurement, not
assumption: at a 12-combo pool (one hero class, one villain class),
solve_flop_turn (max_raises=2, one real sized bet + all-in on both
streets) measured ~18-26s across two different real boards at its
default iteration count, ~59s at its iteration cap; solve_flop_to_river
(max_raises=1, push/fold only — a shallower tree specifically because
of the extra chance-node hop's cost) measured a wider, genuinely
board-dependent ~63-105s *at its default iteration count alone* across
two different real boards (see the cost-shape paragraph below for why
this is more variable than solve_flop_turn's). Both are genuinely
slower than /solve_flop's ~2.6s — shipped anyway, same honesty
precedent as M9's 9-max preflop endpoint (~90s, documented as
noisier/slower rather than hidden), not because they're fast but
because the real numbers make them tolerable for a live (if not snappy)
request. Both endpoints expose only the *flop*-level strategy (the
same response shape /solve_flop already returns) — the deeper solve
improves the accuracy of that flop-level number by baking in real
turn/river action, but the turn/river tree itself
(StrategyResult.chance_data) isn't surfaced here; an interactive
turn/river explorer is a separate, materially bigger feature, not
attempted in this milestone.

solve_flop_turn's cost is close to flat across iteration count (the
exact-CFR path walks the whole tree every iteration, so every chance
node gets built on iteration 1 regardless of how many iterations run
after that) — confirmed live: 200 vs 2000 iterations on the same board
measured ~18s vs ~59s, a real but modest marginal cost (~0.023s/
iteration), not the dominant one. solve_flop_to_river's cost shape
turned out to be genuinely board-dependent in a way solve_flop_turn's
isn't: on one board it stayed close to flat (10 vs 20 iterations
measured ~102s vs ~105s), but M13's own PR measurement on a different
board found it scaling close to linearly with iteration count instead
(50 iterations ~145s, 200 iterations ~218s, versus ~63s at the 20-
iteration default there). Rather than trust either single board's shape
to generalize, MAX_FLOP_TO_RIVER_ITERATIONS is set equal to its own
default — no headroom above it at all — so `iterations` can only ever
be used to get a faster, noisier result on this endpoint, never a
slower one.

GET /solve_flop_cached is M22's deliverable, closing the real-time-
speed roadmap's own explicitly-deferred "wire it into a live endpoint"
follow-on (poker_solver/library.py's module docstring and CLAUDE.md's
"The real-time-speed roadmap" section): board + stack in, hero's
per-combo strategy out, same as /solve_flop, but backed by
poker_solver.library.query_strategy (M21) instead of a plain per-
request cache dict. A hit costs a canonicalize_board call, a dict
.get(), and a handful of translate_combo calls — no CFR, no equity-
table build; a miss falls back to a real solve (via query_strategy's
own build_library call) and caches the canonical result so a later hit
on that spot, or on any real board merely suit-isomorphic to it, is
instant. Only `board`/`stack_bb` are exposed as query params — every
other input (pot, the demo range, raise sizing, iterations) is a fixed
server constant, not because it can't vary in principle but because
none of it is part of query_strategy's canonical (board, bucketed-
stack) cache key: letting any of it vary per request would mean a
later request's stated value could silently describe a *different*
spot than the one actually served on a cache hit, which the response
would then echo back as if it had been honored. Deliberately not
pre-warmed, unlike /solve_flop_turn and /solve_flop_to_river — pre-
warming the frontend's own default board would mean a user's very
first, unmodified click already showed a cache hit, undercutting the
one thing this endpoint exists to demonstrate.

POST /solve_flop_from_path is M24's deliverable, closing the last thing
M21/M22/M23 each still listed as remaining: a live endpoint that
accepts a real, untrusted action-path description end to end (stack in,
big blinds; a sequence of action *kinds* like "raise"/"call_or_check";
a flop board out) rather than a fixed demo range. Chains a real preflop
solve (cached raw, not just formatted, unlike /solve/{stack_bb}'s own
`_get_or_solve`) through `derive_ranges_from_path` (M16) into `query_
strategy_from_path` (M23) — the first endpoint to actually exercise
that connection live. This is the first POST/request-body route in
this app (every other route is GET-with-query-params) — variable-length
structured input (an action sequence) doesn't fit that shape naturally.

Two real problems were found and fixed *before* this shipped, not
after: (1) `derive_ranges_from_path` doesn't prune anything — a real
walked path against the full 169-class preflop pool left both sides'
*entire* class pool nonzero (CFR+'s own floating-point floor, not a
meaningful signal), a ~1,176-combo union that would cost on the order
of hours per request fed straight into `solve_flop`'s O(N²) equity-
table build. Fixed by capping the *derived* range to the top
MAX_PATH_QUERY_CLASSES_PER_SIDE highest-frequency classes per side at
this API layer (via `dataclasses.replace` on the `PathScenario` — the
engine layer itself, `derive_ranges_from_path`/`query_strategy_from_
path`, is untouched and stays exactly as general as M16/M23 built it),
measured for real at ~82 combos / ~21s per miss on a real 3-step path.
(2) Unlike /solve_flop_cached's one shared `_flop_query_library` (safe
there only because its range/pot are fixed constants, identical on
every call), this endpoint's range/pot are derived fresh per request
from each client's own action_path — sharing one dict would let two
unrelated real situations that happen to canonicalize to the same
(board, stack-bucket) key silently return each other's answer. Fixed
with a partitioned `_path_query_libraries`, one dict per distinct
(action_path, stack_bb, iterations), never a single shared one.

POST /preflop_walk is M25's deliverable, the companion endpoint
/solve_flop_from_path's own docstring (and CLAUDE.md's v3 vision) always
said this app was still missing: "what's legal from here," a board-
independent, boardless preflop-tree-only query (stack in, an action path
so far, the legal actions and pot/live-position state at the resulting
node out), letting a real frontend build an action_path one legal click
at a time instead of only offering curated presets. Reuses `_get_or_
solve_preflop_raw`'s same cache /solve_flop_from_path already populates
(walking to a mid-tree node needs no range derivation, no board, no
equity table, no `query_strategy` at all — so none of the partitioned-
library machinery above applies here, and none was added). `_resolve_
action_path` (M24) now returns the final node it walks to, not just the
Action list, since this endpoint needs to inspect that node directly
rather than hand it to `derive_ranges_from_path`. A terminal node is not
automatically a *postflop-eligible* terminal — a fold-out is terminal
with only one live position, which `/solve_flop_from_path` would 422 on
— so the response reports `live_positions` explicitly rather than
leaving the caller to guess from `is_terminal` alone.

POST /solve_turn_from_path is M26's deliverable: real turn-level advice,
not just a flop-level number improved by real turn action baked in.
`solve_flop_turn` (M12) already runs one CFR solve over the *entire*
joint flop+turn tree, and its `StrategyResult.chance_data` dict already
holds a real, fully-solved turn `DecisionNode` for every dealt-card
branch — nothing before this milestone ever walked in and read one out
(this module's own docstring said so, unchanged since M14: "an
interactive turn/river explorer is a separate, materially bigger
feature, not attempted"). Measured, not assumed, before committing to
this: reading chance_data *is* free — walking into one specific branch
and calling `strategy_at()` on it costs ~0.04ms, confirmed separately
from the solve that produces it. Zero new engine code was needed in
`poker_solver/` for this milestone.

**A real, caught-the-hard-way finding along the way, not smoothed
over**: an early version reused `MAX_PATH_QUERY_CLASSES_PER_SIDE`
directly for this endpoint's own range cap, on the assumption that a
6-class-per-side cap costing `/solve_flop_from_path` ~21s/miss would
cost roughly the same here. A real end-to-end request instead measured
**454s**. Root cause: `solve_flop_turn`'s cost curve is fundamentally
steeper than `solve_flop`-via-`query_strategy`'s — it builds ~49 branch
equity tables per chance-eligible flop terminal, not one table total —
so the same 6-class cap (which expands to a *combo* count depending on
how many of those classes are pairs/suited/offsuit, not a fixed number)
produced a 58-combo pool here, not the ~12-combo pool a hand-picked
demo range measured during planning. Fixed with `MAX_TURN_PATH_QUERY_
CLASSES_PER_SIDE`, this endpoint's own, separately-measured cap (see
its own comment for the real numbers at cap=1/2/3/6) — landing this
endpoint's real cost in the same already-accepted "slow but tolerable
for a live, if not snappy, request" bracket `/solve_flop_to_river`
established at M14 (~63-105s), not the ~18-26s this docstring
originally, incorrectly, expected to carry over.

Chains three existing pieces, none of them modified: `_get_or_solve_
preflop_raw` + `_resolve_action_path` + `derive_ranges_from_path` for
the preflop leg (identical to `/solve_flop_from_path`'s own first
stage); `solve_flop_turn` (M12) for the flop+turn solve, behind its own
plain-dict cache (`_turn_path_cache`) — deliberately narrow-keyed: the
key covers only what solve_flop_turn's own cost actually depends on
(the preflop leg, the board, both iteration counts), and deliberately
*excludes* `flop_action_path`/`turn_card` — including them would force
a full re-solve (tens of seconds, see MAX_TURN_PATH_QUERY_CLASSES_PER_
SIDE's own comment for the real numbers) for every different turn-card
query against an otherwise-identical situation, defeating this
milestone's own "reading chance_data afterward is free" finding;
`_resolve_action_path` again,
reused unchanged a second time in the same request, now walking the
flop_turn result's own root instead of the preflop result's, to find
which of its chance-eligible terminals the client's flop_action_path
actually reaches.

Two real checks, present in `library.query_strategy_from_path` but
silently lost without them since this endpoint deliberately bypasses
that function (its canonical-library machinery doesn't fit a per-turn-
card query shape), are ported here explicitly: `path_scenario.node`
must be a `TerminalNode` (the preflop round actually closed), and both
live positions' remaining stacks must be equal (proven, not merely
checked, to always hold at a real terminal — see `query_strategy_from_
path`'s own docstring for the full argument; kept here as a defensive
`RuntimeError`, the same "should be impossible, cross-checked anyway"
precedent that function already set). Without them, an unclosed preflop
path would silently feed one side's stack into `solve_flop_turn` as if
both players shared it, instead of erroring cleanly.

`iterations` (preflop leg) and `turn_iterations` (the solve_flop_turn
leg) are two independent request fields, not one shared value —
deliberately, not an oversight: /solve_flop_from_path's own flop-stage
iterations are fixed/unexposed specifically because that leg sits
behind query_strategy's canonical-library abstraction (a client-varying
value would be silently ignored on a hit); this endpoint doesn't use
that abstraction at all, so nothing forces the two legs' costs to share
one cap, and doing so anyway would silently under-cap preflop
convergence 10x below every sibling endpoint's own MAX_ITERATIONS for a
reason (turn-solve cost) that has nothing to do with preflop's own
(much cheaper) cost.

Only the *first* turn decision is ever exposed (`branch.root` itself,
never a deeper turn-street path) — a deliberate scope cut mirroring
query_strategy's own opening-node-only precedent, not an oversight. An
interactive "what's legal on the turn from here" walker, and river-
level advice one street further, are the natural next milestones this
one deliberately defers — both already de-risked cost-wise by this
milestone's own measurements (a two-hop river walk measured ~0.002ms
after a real solve_flop_to_river call), unlike every prior open
question in this project's real-time-speed thread.
"""

import dataclasses
import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path as FilePath

from fastapi import FastAPI, HTTPException, Path, Query
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from poker_solver.board_equity import two_combo_equity
from poker_solver.canonicalize import canonical_stack_depth, canonicalize_board
from poker_solver.cards import parse_cards
from poker_solver.combos import HandCombo, range_from_class_frequencies
from poker_solver.equity import MultiwayEquityCache
from poker_solver.game_tree import CALL_OR_CHECK, DecisionNode, GameConfig, TerminalNode, resolve_action
from poker_solver.library import query_strategy, query_strategy_from_path
from poker_solver.solver import (
    DEFAULT_FLOP_ITERATIONS,
    DEFAULT_FLOP_TO_RIVER_ITERATIONS,
    DEFAULT_FLOP_TURN_ITERATIONS,
    DEFAULT_ITERATIONS,
    StrategyResult,
    derive_ranges_from_path,
    solve_flop,
    solve_flop_to_river,
    solve_flop_turn,
    solve_preflop,
)
from poker_solver.starting_hands import StartingHand
from poker_solver.strategy_format import format_flop_response, format_solve_response

from .schemas import (
    ActionPathRequest,
    EquityResponse,
    FlopPathQueryResponse,
    FlopQueryResponse,
    FlopSolveResponse,
    PreflopWalkRequest,
    PreflopWalkResponse,
    SolveResponse,
    TurnPathQueryResponse,
    TurnPathRequest,
)

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

# /solve_flop_turn's and /solve_flop_to_river's curated demo pool (M14)
# — deliberately separate from, and much smaller than, DEMO_FLOP_HERO_/
# VILLAIN_CLASSES above (which stays serving only /solve_flop). Shared
# between both new endpoints, not two separate pools, so a frontend
# "runout depth" selector compares the *same* matchup at different
# depths. Sized from real measurement, not assumption — see the module
# docstring for the numbers this pool size is grounded in; a wider pool
# was tried and rejected during M14 for pushing solve_flop_to_river well
# past its own iteration-cap budget below.
DEMO_CHAINED_FLOP_HERO_CLASSES = {
    StartingHand("A", "A"): 1.0,
}
DEMO_CHAINED_FLOP_VILLAIN_CLASSES = {
    StartingHand("Q", "Q"): 1.0,
}

# Matches FlopSolver.tsx's DEFAULT_BOARD/DEFAULT_POT/DEFAULT_STACK_BB —
# used only to pick what to pre-warm below; kept in sync manually (a
# drift here just makes the pre-warm quietly stop helping the real
# common case, not a correctness bug).
DEFAULT_CHAINED_FLOP_BOARD = "Jh7d2c"

# /solve_flop_cached's curated demo pool (M22) — deliberately separate
# from every other endpoint's own pool, same "each endpoint gets its
# own" precedent DEMO_FLOP_HERO_/VILLAIN_CLASSES and DEMO_CHAINED_
# FLOP_HERO_/VILLAIN_CLASSES above already establish. Sized to the same
# ~23-combo scale CLAUDE.md's M17/M18/M20/M21 entries measured against
# (2 hero classes / 2 villain classes) — see CLAUDE.md's M22 entry for
# this endpoint's own fresh measurement.
FLOP_QUERY_HERO_CLASSES = {
    StartingHand("A", "A"): 1.0,
    StartingHand("A", "K", suited=True): 1.0,
}
FLOP_QUERY_VILLAIN_CLASSES = {
    StartingHand("K", "K"): 1.0,
    StartingHand("7", "2", suited=False): 1.0,
}

# Fixed, not query params — see the module docstring's /solve_flop_
# cached paragraph for why: neither is part of query_strategy's
# canonical (board, bucketed-stack) cache key, so letting either vary
# per request would risk a later request's stated value silently not
# matching what a cache hit actually returns.
FLOP_QUERY_POT = 10.0
FLOP_QUERY_ITERATIONS = DEFAULT_FLOP_ITERATIONS

# /solve_flop_from_path's (M24) own cost controls. MAX_PATH_QUERY_
# CLASSES_PER_SIDE is not a fixed demo pool like the constants above —
# derive_ranges_from_path's own output is capped down to this many
# highest-frequency classes per side at request time (see the module
# docstring's Finding 1: an uncapped real path left the *entire*
# 169-class pool nonzero, a ~1,176-combo union that would cost hours
# per request). Measured for real at this value: ~82 combos, ~21s per
# miss on a real 3-step path — a deliberate, moderately larger cap than
# FLOP_QUERY_HERO_/VILLAIN_CLASSES' own 2-classes-per-side, since this
# endpoint's whole point is representing a real derived situation, not
# a curated demo, so keeping more of the genuinely relevant range is
# worth the extra cost. MAX_PATH_LENGTH is cheap insurance, not load-
# bearing — the tree structurally bounds a real legal path to roughly
# 2*max_raises regardless, an oversized list fails fast at the first
# illegal step either way.
MAX_PATH_QUERY_CLASSES_PER_SIDE = 6
MAX_PATH_LENGTH = 20
# Flop-stage iterations, fixed — not exposed, unlike the preflop-stage
# iterations request field below. This part of the pipeline sits behind
# query_strategy's canonical-library abstraction, where a client-
# varying value would be silently ignored on a cache hit — the exact
# bug class /solve_flop_cached's own design principle already guards
# against; the preflop-stage solve, by contrast, is a plain per-request
# cache dict (see _get_or_solve_preflop_raw), so exposing *its* own
# iterations is exactly as safe as /solve/{stack_bb} already relies on.
PATH_QUERY_ITERATIONS = DEFAULT_FLOP_ITERATIONS

# Fixed server-side constants, not query params — same reasoning
# DEMO_FLOP_HERO_/VILLAIN_CLASSES already establish (letting a client
# control tree size/range directly is an unbounded-cost door). Different
# max_raises per endpoint: solve_flop_to_river's extra chance-node hop
# is expensive enough (see the module docstring's per-iteration cost
# finding) that it needs a shallower tree than solve_flop_turn to stay
# in a tolerable-for-a-live-request budget.
FLOP_TURN_MAX_RAISES = 2
FLOP_TURN_RAISE_SIZES = (2.5,)
FLOP_TO_RIVER_MAX_RAISES = 1
FLOP_TO_RIVER_RAISE_SIZES = ()

# See the module docstring: solve_flop_turn's cost is close to flat
# across iteration count (every chance node is built on iteration 1
# regardless) — confirmed live at the cap (200 iters ~18-26s depending
# on board, 2000 iters ~59s), so a generous cap is safe. solve_flop_to_
# river's cost is much more board-dependent and, on at least one real
# board, scales close to linearly with iterations rather than staying
# flat — confirmed live that its DEFAULT (20 iterations) alone already
# costs ~63-105s depending on board, wider variance than expected from
# the single board M13's own PR measured. Given that, MAX_FLOP_TO_RIVER_
# ITERATIONS is set equal to its own default rather than leaving any
# headroom above it — the `iterations` query param can only be used to
# get a faster, noisier result on this endpoint, never a slower one.
MAX_FLOP_TURN_ITERATIONS = 2_000
MAX_FLOP_TO_RIVER_ITERATIONS = DEFAULT_FLOP_TO_RIVER_ITERATIONS

# /solve_turn_from_path's OWN class cap, deliberately separate from
# MAX_PATH_QUERY_CLASSES_PER_SIDE — a real, measured finding this
# milestone made the hard way (a first draft reused that constant
# directly and a real request measured 454s). MAX_PATH_QUERY_CLASSES_
# PER_SIDE=6 was calibrated against /solve_flop_from_path's own
# solve_flop-via-query_strategy cost profile; this endpoint's
# solve_flop_turn call is a fundamentally steeper curve (build_chance_
# node's ~49 branch equity tables per chance-eligible terminal, not
# solve_flop's single table), so the same class count produces a much
# larger combo pool and a much worse cost. Measured for real, same
# preflop line/board/iterations throughout: cap=6 -> 58 combos, 454s;
# cap=3 -> 27 combos, 99.9s; cap=2 -> 19 combos, 45.9s; cap=1 -> 7
# combos, 10.2s. Set to 2 — in the same "slow but tolerable for a live,
# if not snappy, request" bracket /solve_flop_to_river was already
# accepted in at M14 (~63-105s), while keeping more range diversity
# than capping to a single class per side would.
MAX_TURN_PATH_QUERY_CLASSES_PER_SIDE = 2

_cache: dict = {}
_cache_lock = threading.Lock()
_multiway_cache: dict = {}
_multiway_lock = threading.Lock()
_flop_cache: dict = {}
_flop_lock = threading.Lock()
# Deliberately separate from _flop_cache and from each other, not one
# shared dict — the cache key (board, pot, stack_bb, iterations) omits
# max_raises/raise_sizes/the demo pool because those are fixed constants
# per endpoint, not request-varying, which is only safe *because* each
# endpoint has its own dict. A shared dict would let an identical key
# collide between two endpoints with different max_raises.
_flop_turn_cache: dict = {}
_flop_turn_lock = threading.Lock()
_flop_to_river_cache: dict = {}
_flop_to_river_lock = threading.Lock()
# Not "_flop_query_cache" — this dict IS query_strategy's own `library`
# parameter (poker_solver/library.py), held at module scope across
# requests, a different granularity than the four dicts above (which
# each cache one formatted response/StrategyResult, not a canonical-
# key -> LibraryEntry mapping). _flop_query_lock is held for query_
# strategy's ENTIRE call in _query_flop below, not just around a dict
# read/write the way the four helpers above do — see _query_flop's own
# docstring for why that's a deliberate, stricter departure.
_flop_query_library: dict = {}
_flop_query_lock = threading.Lock()

_preflop_raw_cache: dict = {}
_preflop_raw_lock = threading.Lock()
# Deliberately NOT one shared dict like _flop_query_library above — see
# the module docstring's Finding 2. This endpoint's range/pot are
# derived fresh per request from each client's own action_path, unlike
# /solve_flop_cached's fixed demo range, so a shared canonical (board,
# stack) key could silently serve one real situation's answer to an
# unrelated one. One private library per distinct (action_path,
# stack_bb, iterations) instead.
_path_query_libraries: dict = {}
_path_query_lock = threading.Lock()

# M26's own plain-dict cache for solve_flop_turn results, deliberately
# separate from every dict above. Keyed narrowly — only what solve_
# flop_turn's own cost actually depends on (preflop_action_path,
# stack_bb, its own iterations, board, turn_iterations) — deliberately
# NOT flop_action_path/turn_card, which are resolved by walking the
# already-solved tree afterward (~0.04ms, measured) rather than by
# re-solving; including them in the key would force a full re-solve per
# distinct turn-card query against an identical situation, defeating
# this endpoint's whole point. Locking mirrors _get_or_solve_flop_
# turn's own looser discipline (around the dict access only, not the
# whole solve) — not query_strategy's atomic whole-call lock, since
# this isn't going through that primitive.
_turn_path_cache: dict = {}
_turn_path_lock = threading.Lock()


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


def _get_or_solve_flop_turn(board_cards: tuple, pot: float, stack_bb: float, iterations: int) -> StrategyResult:
    """Solves (or returns the cached result of solving) DEMO_CHAINED_FLOP_
    HERO/VILLAIN_CLASSES' board-legal expansion via solve_flop_turn — same
    shape as _get_or_solve_flop, own cache dict (see its module-level
    comment for why a shared one would be unsafe)."""
    key = (board_cards, round(pot, 2), round(stack_bb), iterations)
    with _flop_turn_lock:
        cached = _flop_turn_cache.get(key)
    if cached is not None:
        return cached

    exclude = frozenset(board_cards)
    hero_range = range_from_class_frequencies(DEMO_CHAINED_FLOP_HERO_CLASSES, exclude=exclude)
    villain_range = range_from_class_frequencies(DEMO_CHAINED_FLOP_VILLAIN_CLASSES, exclude=exclude)
    if not hero_range or not villain_range:
        raise ValueError(f"board {''.join(str(c) for c in board_cards)!r} blocks every demo-range combo")

    result = solve_flop_turn(
        board=board_cards,
        hero_range=hero_range,
        villain_range=villain_range,
        pot=pot,
        effective_stack_bb=stack_bb,
        raise_sizes=FLOP_TURN_RAISE_SIZES,
        max_raises=FLOP_TURN_MAX_RAISES,
        iterations=iterations,
    )

    with _flop_turn_lock:
        _flop_turn_cache[key] = result
    return result


def _get_or_solve_flop_to_river(board_cards: tuple, pot: float, stack_bb: float, iterations: int) -> StrategyResult:
    """Same idea as _get_or_solve_flop_turn, via solve_flop_to_river and
    its own (much tighter — see MAX_FLOP_TO_RIVER_ITERATIONS) cache."""
    key = (board_cards, round(pot, 2), round(stack_bb), iterations)
    with _flop_to_river_lock:
        cached = _flop_to_river_cache.get(key)
    if cached is not None:
        return cached

    exclude = frozenset(board_cards)
    hero_range = range_from_class_frequencies(DEMO_CHAINED_FLOP_HERO_CLASSES, exclude=exclude)
    villain_range = range_from_class_frequencies(DEMO_CHAINED_FLOP_VILLAIN_CLASSES, exclude=exclude)
    if not hero_range or not villain_range:
        raise ValueError(f"board {''.join(str(c) for c in board_cards)!r} blocks every demo-range combo")

    result = solve_flop_to_river(
        board=board_cards,
        hero_range=hero_range,
        villain_range=villain_range,
        pot=pot,
        effective_stack_bb=stack_bb,
        raise_sizes=FLOP_TO_RIVER_RAISE_SIZES,
        max_raises=FLOP_TO_RIVER_MAX_RAISES,
        iterations=iterations,
    )

    with _flop_to_river_lock:
        _flop_to_river_cache[key] = result
    return result


def _query_flop(board_cards: tuple, stack_bb: float) -> dict:
    """Canonicalize-then-lookup-then-fallback-to-solve (M21's query_
    strategy) over FLOP_QUERY_HERO_/VILLAIN_CLASSES' board-legal
    expansion. Named _query_flop, not _get_or_solve_flop_X — the
    check-or-solve duality those helpers hand-roll around their own
    cache dict already lives inside query_strategy itself here.

    Holds _flop_query_lock for query_strategy's ENTIRE call, not just
    around a dict read/write the way every _get_or_solve_X helper above
    does — a deliberate, stricter departure. query_strategy is an
    atomic check-then-maybe-solve-then-insert primitive with no
    concurrency control of its own (see its own docstring's "Known,
    deliberate limitations"); unlike the hand-rolled helpers above, it
    can't be decomposed into a separate check step and solve step
    without reimplementing its internals here. This closes that
    documented gap for this one live entry point: no concurrent-miss
    double-solve, period. Real cost, not hidden: two unrelated
    concurrent misses (different, non-isomorphic boards) now queue
    behind each other rather than solving in parallel — the mirror-
    image tradeoff of every other endpoint's own looser locking (no
    serialization at all, so a concurrent identical-key miss there
    really can double-solve). _flop_query_lock is its own independent
    threading.Lock() object, and every request is dispatched via
    run_in_threadpool onto its own worker thread regardless of
    endpoint, so this only serializes requests to this one endpoint.
    """
    exclude = frozenset(board_cards)
    hero_range = range_from_class_frequencies(FLOP_QUERY_HERO_CLASSES, exclude=exclude)
    villain_range = range_from_class_frequencies(FLOP_QUERY_VILLAIN_CLASSES, exclude=exclude)
    if not hero_range or not villain_range:
        # query_strategy/build_library don't guard this themselves (see
        # poker_solver/library.py) — mirrors _get_or_solve_flop's own
        # guard, but is a genuine second computation, not a reused one:
        # build_library takes raw classes, not pre-computed ranges, so
        # it re-derives these internally regardless. Still cheap: no
        # solve, no equity table, just combo enumeration.
        raise ValueError(f"board {''.join(str(c) for c in board_cards)!r} blocks every demo-range combo")

    with _flop_query_lock:
        result = query_strategy(
            _flop_query_library,
            board=board_cards,
            hero_classes=FLOP_QUERY_HERO_CLASSES,
            villain_classes=FLOP_QUERY_VILLAIN_CLASSES,
            pot=FLOP_QUERY_POT,
            effective_stack_bb=stack_bb,
            iterations=FLOP_QUERY_ITERATIONS,
        )

    # Pure functions of (board_cards, stack_bb) alone, safe to compute
    # outside the lock — guaranteed to agree with whatever key query_
    # strategy just looked up or inserted under (see its own docstring's
    # determinism argument for why).
    canonical_board, _ = canonicalize_board(board_cards)
    canonical_stack_bb = canonical_stack_depth(stack_bb)

    return {
        "board": "".join(str(c) for c in board_cards),
        "canonical_board": "".join(str(c) for c in canonical_board),
        "pot": FLOP_QUERY_POT,
        "stack_bb": stack_bb,
        "canonical_stack_bb": canonical_stack_bb,
        "hit": result.hit,
        "elapsed_seconds": result.elapsed_seconds,
        "strategy": result.strategy,
        "position": "OOP",
        "positions": ["OOP", "IP"],
    }


def _get_or_solve_preflop_raw(stack_bb: float, iterations: int) -> StrategyResult:
    """Solves (or returns the cached result of solving) a real heads-up
    preflop spot, caching the RAW StrategyResult — unlike _get_or_solve
    above, which formats the result and discards it. M24 needs the real
    tree/node_data to walk with derive_ranges_from_path, the same
    reason _get_or_solve_multiway already caches its own raw result
    rather than a formatted one. Its own cache dict, not _cache — a
    formatted dict and a raw StrategyResult are different
    representations, never mixed in one dict (mirrors every other
    cache-dict boundary in this file)."""
    key = _cache_key(stack_bb, iterations)
    with _preflop_raw_lock:
        cached = _preflop_raw_cache.get(key)
    if cached is not None:
        return cached

    result = solve_preflop(stack_bb=stack_bb, iterations=iterations)

    with _preflop_raw_lock:
        _preflop_raw_cache[key] = result
    return result


def _resolve_action_path(root: DecisionNode, action_kinds: list) -> tuple:
    """Turns a client-supplied list of bare action *kind* strings (e.g.
    ["raise", "call_or_check"]) into the real Action objects derive_
    ranges_from_path needs, walking the tree one step at a time via
    game_tree.resolve_action. Raises ValueError prefixed with the
    (0-indexed) step number that failed — friendlier than a bare
    tree-level error for an untrusted caller who can't see the tree.

    Returns (actions, node) — the resolved Action list *and* the node
    the walk actually ends at. M24's original version returned only the
    action list (all it needed for derive_ranges_from_path); M25's
    _preflop_walk needs the node itself, to inspect what's legal *from
    here* without also requiring a resolved next step.

    Explicitly checks isinstance(node, DecisionNode) before resolving
    each step — a TerminalNode has no legal_actions at all (calling
    resolve_action on one would raise a raw AttributeError, not a clean
    ValueError), so an action_path that runs past a real terminal needs
    to be caught here, one step before derive_ranges_from_path's own
    "path continues past a TerminalNode" check would otherwise catch it
    on the already-fully-built Action list.
    """
    actions = []
    node = root
    for step, kind in enumerate(action_kinds):
        if not isinstance(node, DecisionNode):
            raise ValueError(f"step {step}: the hand is already over — no more actions are legal")
        try:
            action = resolve_action(node, kind)
        except ValueError as exc:
            raise ValueError(f"step {step}: {exc}") from exc
        actions.append(action)
        node = node.children[action]
    return actions, node


def _preflop_walk(stack_bb: float, action_kinds: list, iterations: int) -> dict:
    """Orchestrates POST /preflop_walk: a real (cached, raw) preflop
    solve -> resolve the client's bare action kinds into real Actions,
    walking to the resulting node -> report what's legal from there.

    No range derivation, no board, no query_strategy — this is a pure
    tree-state query, so none of _query_flop_from_path's range-capping
    or partitioned-library machinery applies here.
    """
    preflop_result = _get_or_solve_preflop_raw(stack_bb, iterations)
    _actions, node = _resolve_action_path(preflop_result.root, action_kinds)
    live_positions = [p for p in preflop_result.config.positions if p not in node.folded]

    if isinstance(node, TerminalNode):
        return {
            "stack_bb": stack_bb,
            "action_path": list(action_kinds),
            "is_terminal": True,
            "player_to_act": None,
            "live_positions": live_positions,
            "pot": node.pot,
            "legal_actions": [],
        }

    # Safe over ALL positions (folded included), not just live ones:
    # FOLD is only ever offered when to_call > 0 at that instant (see
    # game_tree._build), so the position actually holding the current
    # max can never fold; every other action only ever raises the
    # acting position's invested to >= the pre-action max. So the true
    # max across live positions is monotonically non-decreasing along
    # any path, and a folded position's frozen invested can never
    # exceed a later node's true max — max(node.invested.values()) over
    # every position, folded or not, always equals the live max.
    current_bet = max(node.invested.values())
    to_call = current_bet - node.invested[node.player_to_act]

    legal_actions = []
    for action in node.legal_actions:
        option = {"kind": action.kind, "size": None, "to_call": None}
        if action.kind == CALL_OR_CHECK:
            option["to_call"] = to_call
        elif action.size is not None:
            option["size"] = action.size
        legal_actions.append(option)

    return {
        "stack_bb": stack_bb,
        "action_path": list(action_kinds),
        "is_terminal": False,
        "player_to_act": node.player_to_act,
        "live_positions": live_positions,
        "pot": node.pot,
        "legal_actions": legal_actions,
    }


def _cap_range(range_dict: dict, max_classes: int) -> dict:
    """Top MAX_PATH_QUERY_CLASSES_PER_SIDE classes by frequency, not
    alphabetical/random — a solved strategy has already ranked classes
    by relevance (see the module docstring's Finding 1: an uncapped
    real path left the entire 169-class pool nonzero, a ~1,176-combo
    union that would cost hours per request)."""
    if len(range_dict) <= max_classes:
        return range_dict
    top_items = sorted(range_dict.items(), key=lambda item: item[1], reverse=True)[:max_classes]
    return dict(top_items)


def _query_flop_from_path(action_kinds: list, stack_bb: float, board_cards: tuple, iterations: int) -> dict:
    """Orchestrates POST /solve_flop_from_path end to end: a real
    (cached, raw) preflop solve -> resolve the client's bare action
    kinds into real Actions -> derive_ranges_from_path (M16) -> cap
    both sides to MAX_PATH_QUERY_CLASSES_PER_SIDE (Finding 1) -> a
    private, per-(action_path, stack_bb, iterations) library (Finding
    2, not the shared _flop_query_library _query_flop above uses) ->
    query_strategy_from_path (M23).

    Holds _path_query_lock for the entire query_strategy_from_path
    call, mirroring _query_flop's own stricter-than-the-hand-rolled-
    helpers locking discipline, for the same reason (query_strategy is
    an atomic primitive with no concurrency control of its own). One
    single lock guards every partition, not one lock per partition —
    a deliberate simplicity choice: this endpoint is already expensive
    per miss (~21s, see the module docstring) and low-traffic by
    design (a demo, not a high-throughput service), so the extra
    complexity of per-partition locking isn't earning its keep yet.
    """
    preflop_result = _get_or_solve_preflop_raw(stack_bb, iterations)
    actions, _node = _resolve_action_path(preflop_result.root, action_kinds)
    path_scenario = derive_ranges_from_path(preflop_result, actions)

    capped_ranges = {
        position: _cap_range(range_dict, MAX_PATH_QUERY_CLASSES_PER_SIDE)
        for position, range_dict in path_scenario.ranges.items()
    }
    capped_scenario = dataclasses.replace(path_scenario, ranges=capped_ranges)

    exclude = frozenset(board_cards)
    for position, range_dict in capped_scenario.ranges.items():
        if not range_from_class_frequencies(range_dict, exclude=exclude):
            # Mirrors _query_flop's own guard — cheap, re-derived, no
            # solve cost (query_strategy_from_path/build_library would
            # otherwise silently run with an all-zero reach vector for
            # this position instead of a clear error).
            raise ValueError(
                f"board {''.join(str(c) for c in board_cards)!r} blocks every combo in "
                f"{position}'s derived (capped) range"
            )

    ip_position, oop_position = preflop_result.config.positions
    partition_key = (tuple(action_kinds), round(stack_bb), iterations)
    with _path_query_lock:
        library = _path_query_libraries.setdefault(partition_key, {})
        result = query_strategy_from_path(
            library,
            preflop_result,
            capped_scenario,
            board_cards,
            iterations=PATH_QUERY_ITERATIONS,
        )

    effective_stack_bb = path_scenario.stacks[oop_position]
    canonical_board, _ = canonicalize_board(board_cards)
    canonical_stack_bb = canonical_stack_depth(effective_stack_bb)

    return {
        "board": "".join(str(c) for c in board_cards),
        "canonical_board": "".join(str(c) for c in canonical_board),
        "action_path": list(action_kinds),
        "stack_bb": stack_bb,
        "effective_stack_bb": effective_stack_bb,
        "canonical_stack_bb": canonical_stack_bb,
        "pot": path_scenario.pot,
        "hit": result.hit,
        "elapsed_seconds": result.elapsed_seconds,
        "strategy": result.strategy,
        "position": oop_position,
        "positions": [oop_position, ip_position],
    }


def _query_turn_from_path(
    preflop_action_kinds: list,
    flop_action_kinds: list,
    turn_card,
    stack_bb: float,
    board_cards: tuple,
    iterations: int,
    turn_iterations: int,
) -> dict:
    """Orchestrates POST /solve_turn_from_path end to end: a real
    (cached, raw) preflop solve -> resolve the client's preflop action
    kinds -> derive_ranges_from_path (M16) -> cap both sides (Finding 1,
    same as /solve_flop_from_path) -> solve_flop_turn (M12), behind its
    own narrowly-keyed cache -> resolve the client's flop action kinds
    against *that* result's own root -> deal the client's real turn
    card -> read whatever real strategy solve_flop_turn already computed
    there. See the module docstring for the full design writeup.
    """
    preflop_result = _get_or_solve_preflop_raw(stack_bb, iterations)
    preflop_actions, _node = _resolve_action_path(preflop_result.root, preflop_action_kinds)
    path_scenario = derive_ranges_from_path(preflop_result, preflop_actions)

    # Ported from library.query_strategy_from_path (bypassed here — its
    # canonical-library machinery doesn't fit this endpoint's per-turn-
    # card query shape) — not specific to that abstraction, still
    # required: derive_ranges_from_path itself does not require the
    # preflop action to have closed.
    if not isinstance(path_scenario.node, TerminalNode):
        raise ValueError("preflop_action_path does not reach a terminal — action isn't capped yet")
    ip_position, oop_position = preflop_result.config.positions
    oop_stack = path_scenario.stacks[oop_position]
    ip_stack = path_scenario.stacks[ip_position]
    if oop_stack != ip_stack:
        raise RuntimeError(
            "derive_ranges_from_path returned unequal stacks at a terminal node — should be "
            "impossible per game_tree.py's no-side-pots invariant, please report"
        )
    effective_stack_bb = oop_stack

    # MAX_TURN_PATH_QUERY_CLASSES_PER_SIDE, not MAX_PATH_QUERY_CLASSES_
    # PER_SIDE — see that constant's own comment for the real measured
    # reason (solve_flop_turn's cost curve is fundamentally steeper than
    # solve_flop_from_path's own query_strategy-backed one).
    capped_ranges = {
        position: _cap_range(range_dict, MAX_TURN_PATH_QUERY_CLASSES_PER_SIDE)
        for position, range_dict in path_scenario.ranges.items()
    }
    exclude = frozenset(board_cards)
    for position, range_dict in capped_ranges.items():
        if not range_from_class_frequencies(range_dict, exclude=exclude):
            raise ValueError(
                f"board {''.join(str(c) for c in board_cards)!r} blocks every combo in "
                f"{position}'s derived (capped) range"
            )
    hero_range = range_from_class_frequencies(capped_ranges[oop_position], exclude=exclude)
    villain_range = range_from_class_frequencies(capped_ranges[ip_position], exclude=exclude)

    turn_solve_key = (tuple(preflop_action_kinds), round(stack_bb), iterations, board_cards, turn_iterations)
    with _turn_path_lock:
        result = _turn_path_cache.get(turn_solve_key)
    if result is None:
        result = solve_flop_turn(
            board=board_cards,
            hero_range=hero_range,
            villain_range=villain_range,
            pot=path_scenario.pot,
            effective_stack_bb=effective_stack_bb,
            positions=(oop_position, ip_position),
            raise_sizes=FLOP_TURN_RAISE_SIZES,
            max_raises=FLOP_TURN_MAX_RAISES,
            iterations=turn_iterations,
        )
        with _turn_path_lock:
            _turn_path_cache[turn_solve_key] = result

    _flop_actions, flop_node = _resolve_action_path(result.root, flop_action_kinds)
    if not isinstance(flop_node, TerminalNode):
        raise ValueError("flop_action_path does not reach a terminal — action isn't capped yet")

    response = {
        "board": "".join(str(c) for c in board_cards),
        "turn_card": str(turn_card),
        "preflop_action_path": list(preflop_action_kinds),
        "flop_action_path": list(flop_action_kinds),
        "stack_bb": stack_bb,
        "position": oop_position,
        "positions": [oop_position, ip_position],
        "elapsed_seconds": result.elapsed_seconds,
    }

    if id(flop_node) not in result.chance_data:
        # Heads-up only: proven airtight, not assumed (see the module
        # docstring) — TerminalNode.is_showdown is exactly `len(folded)
        # == 0` for a 2-position tree, and chance_data is only ever
        # populated for showdown-eligible terminals, so "not in
        # chance_data" and "folded" are exactly equivalent here, no
        # ambiguous third case.
        return {
            **response,
            "is_terminal": True,
            "player_to_act": None,
            "strategy": {},
            "pot": flop_node.pot,
            "effective_stack_bb": effective_stack_bb,
        }

    chance_node = result.chance_data[id(flop_node)]
    if turn_card not in chance_node.branches:
        raise ValueError(f"{turn_card} is not a legal turn card here (already on the board, or already dealt)")
    turn_node = chance_node.branches[turn_card].root

    # Recomputed identically to chance.py's build_chance_node's own
    # `remaining_stack` — there is no way to read it back off ChanceNode/
    # ChanceBranch directly, so this must stay hand-in-sync with that
    # formula if it ever changes. Safe over ALL positions' invested
    # (not just one), same max()-over-everyone reasoning _preflop_walk's
    # own to_call computation already relies on.
    remaining_stack = effective_stack_bb - max(chance_node.invested.values())

    if isinstance(turn_node, TerminalNode):
        # The flop action already put a player fully all-in — chance.py's
        # own design reuses the terminal itself as branch.root in that
        # case, never populating a real turn decision node.
        return {
            **response,
            "is_terminal": True,
            "player_to_act": None,
            "strategy": {},
            "pot": turn_node.pot,
            "effective_stack_bb": remaining_stack,
        }

    # Only the FIRST turn decision is ever exposed here (branch.root
    # itself), never a deeper turn-street path — see the module
    # docstring for why that's a deliberate cut, not an oversight.
    strategy = result.strategy_at(turn_node)
    return {
        **response,
        "is_terminal": False,
        "player_to_act": turn_node.player_to_act,
        "strategy": strategy,
        "pot": turn_node.pot,
        "effective_stack_bb": remaining_stack,
    }


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

    # solve_flop itself (~2.6s) isn't worth pre-warming — /solve_flop
    # was never given this treatment, since a couple seconds is a fine
    # cold-start tax. solve_flop_turn/solve_flop_to_river are meaningfully
    # slower (~26s/~63s), so pre-warm one instance of each against the
    # frontend's own default board/pot/stack (FlopSolver.tsx's
    # DEFAULT_BOARD/DEFAULT_POT/DEFAULT_STACK_BB — keep these two in sync
    # if either side's defaults ever change) so a user's very first,
    # overwhelmingly-likely-unmodified click is instant rather than
    # paying the full cost live.
    try:
        logger.info("pre-warming solve_flop_turn for the default board")
        _get_or_solve_flop_turn(tuple(parse_cards(DEFAULT_CHAINED_FLOP_BOARD)), 10.0, 40.0, DEFAULT_FLOP_TURN_ITERATIONS)
    except Exception:
        logger.exception("pre-warm failed for solve_flop_turn")

    try:
        logger.info("pre-warming solve_flop_to_river for the default board")
        _get_or_solve_flop_to_river(
            tuple(parse_cards(DEFAULT_CHAINED_FLOP_BOARD)), 10.0, 40.0, DEFAULT_FLOP_TO_RIVER_ITERATIONS
        )
    except Exception:
        logger.exception("pre-warm failed for solve_flop_to_river")

    # /solve_turn_from_path's own cost (~16-26s) is in the same
    # tax-worth-avoiding bracket the two pre-warms above were already
    # accepted for. Matches TurnPathSolver.tsx's own default preflop/
    # flop presets and board — keep these in sync if either side's
    # defaults ever change (same "kept in sync manually" precedent
    # DEFAULT_CHAINED_FLOP_BOARD's own comment already accepts).
    try:
        logger.info("pre-warming solve_turn_from_path for the default line")
        _query_turn_from_path(
            ["raise", "call_or_check"],
            ["raise", "call_or_check"],
            parse_cards("2h")[0],
            100.0,
            tuple(parse_cards(DEFAULT_CHAINED_FLOP_BOARD)),
            DEFAULT_ITERATIONS,
            DEFAULT_FLOP_TURN_ITERATIONS,
        )
    except Exception:
        logger.exception("pre-warm failed for solve_turn_from_path")


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


@app.get("/solve_flop_turn", response_model=FlopSolveResponse)
async def solve_flop_turn_endpoint(
    board: str = Query(..., description="Exactly 3 cards, e.g. Jh7d2c"),
    pot: float = Query(10.0, gt=0, description="Pot entering the flop"),
    stack_bb: float = Query(40.0, gt=0, description="Effective stack behind, in big blinds"),
    iterations: int = Query(DEFAULT_FLOP_TURN_ITERATIONS, gt=0, le=MAX_FLOP_TURN_ITERATIONS),
    position: str | None = Query(None, description="OOP or IP — defaults to OOP, the first to act"),
):
    try:
        board_cards = tuple(parse_cards(board))
        if len(board_cards) != 3:
            raise ValueError(f"board must have exactly 3 cards for a flop, got {len(board_cards)}")
        result = await run_in_threadpool(_get_or_solve_flop_turn, board_cards, pot, stack_bb, iterations)
        return format_flop_response(result, board="".join(str(c) for c in board_cards), position=position)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/solve_flop_to_river", response_model=FlopSolveResponse)
async def solve_flop_to_river_endpoint(
    board: str = Query(..., description="Exactly 3 cards, e.g. Jh7d2c"),
    pot: float = Query(10.0, gt=0, description="Pot entering the flop"),
    stack_bb: float = Query(40.0, gt=0, description="Effective stack behind, in big blinds"),
    iterations: int = Query(DEFAULT_FLOP_TO_RIVER_ITERATIONS, gt=0, le=MAX_FLOP_TO_RIVER_ITERATIONS),
    position: str | None = Query(None, description="OOP or IP — defaults to OOP, the first to act"),
):
    try:
        board_cards = tuple(parse_cards(board))
        if len(board_cards) != 3:
            raise ValueError(f"board must have exactly 3 cards for a flop, got {len(board_cards)}")
        result = await run_in_threadpool(_get_or_solve_flop_to_river, board_cards, pot, stack_bb, iterations)
        return format_flop_response(result, board="".join(str(c) for c in board_cards), position=position)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/solve_flop_cached", response_model=FlopQueryResponse)
async def solve_flop_cached_endpoint(
    board: str = Query(..., description="Exactly 3 cards, e.g. Jh7d2c"),
    stack_bb: float = Query(40.0, gt=0, description="Effective stack behind, in big blinds"),
):
    try:
        board_cards = tuple(parse_cards(board))
        if len(board_cards) != 3:
            raise ValueError(f"board must have exactly 3 cards for a flop, got {len(board_cards)}")
        return await run_in_threadpool(_query_flop, board_cards, stack_bb)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/solve_flop_from_path", response_model=FlopPathQueryResponse)
async def solve_flop_from_path_endpoint(request: ActionPathRequest):
    try:
        if len(request.action_path) > MAX_PATH_LENGTH:
            raise ValueError(f"action_path is too long ({len(request.action_path)} > {MAX_PATH_LENGTH})")
        board_cards = tuple(parse_cards(request.board))
        if len(board_cards) != 3:
            raise ValueError(f"board must have exactly 3 cards for a flop, got {len(board_cards)}")
        iterations = request.iterations if request.iterations is not None else DEFAULT_ITERATIONS
        if not 0 < iterations <= MAX_ITERATIONS:
            raise ValueError(f"iterations must be between 1 and {MAX_ITERATIONS}, got {iterations}")
        return await run_in_threadpool(
            _query_flop_from_path, request.action_path, request.stack_bb, board_cards, iterations
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/preflop_walk", response_model=PreflopWalkResponse)
async def preflop_walk_endpoint(request: PreflopWalkRequest):
    try:
        if len(request.action_path) > MAX_PATH_LENGTH:
            raise ValueError(f"action_path is too long ({len(request.action_path)} > {MAX_PATH_LENGTH})")
        iterations = request.iterations if request.iterations is not None else DEFAULT_ITERATIONS
        if not 0 < iterations <= MAX_ITERATIONS:
            raise ValueError(f"iterations must be between 1 and {MAX_ITERATIONS}, got {iterations}")
        return await run_in_threadpool(_preflop_walk, request.stack_bb, request.action_path, iterations)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/solve_turn_from_path", response_model=TurnPathQueryResponse)
async def solve_turn_from_path_endpoint(request: TurnPathRequest):
    try:
        if len(request.preflop_action_path) > MAX_PATH_LENGTH:
            raise ValueError(
                f"preflop_action_path is too long ({len(request.preflop_action_path)} > {MAX_PATH_LENGTH})"
            )
        if len(request.flop_action_path) > MAX_PATH_LENGTH:
            raise ValueError(f"flop_action_path is too long ({len(request.flop_action_path)} > {MAX_PATH_LENGTH})")
        board_cards = tuple(parse_cards(request.board))
        if len(board_cards) != 3:
            raise ValueError(f"board must have exactly 3 cards for a flop, got {len(board_cards)}")
        turn_cards = tuple(parse_cards(request.turn_card))
        if len(turn_cards) != 1:
            raise ValueError(f"turn_card must have exactly 1 card, got {len(turn_cards)}")
        iterations = request.iterations if request.iterations is not None else DEFAULT_ITERATIONS
        if not 0 < iterations <= MAX_ITERATIONS:
            raise ValueError(f"iterations must be between 1 and {MAX_ITERATIONS}, got {iterations}")
        turn_iterations = (
            request.turn_iterations if request.turn_iterations is not None else DEFAULT_FLOP_TURN_ITERATIONS
        )
        if not 0 < turn_iterations <= MAX_FLOP_TURN_ITERATIONS:
            raise ValueError(
                f"turn_iterations must be between 1 and {MAX_FLOP_TURN_ITERATIONS}, got {turn_iterations}"
            )
        return await run_in_threadpool(
            _query_turn_from_path,
            request.preflop_action_path,
            request.flop_action_path,
            turn_cards[0],
            request.stack_bb,
            board_cards,
            iterations,
            turn_iterations,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# Registered last so it only catches requests /solve doesn't match —
# Starlette checks routes in registration order, and a Mount only
# matches as a fallback for paths its earlier siblings didn't claim.
# html=True serves frontend/dist/index.html for "/" and other paths
# (client-side routing would need this too, though this app has none).
if FRONTEND_DIST_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST_DIR), html=True), name="frontend")
