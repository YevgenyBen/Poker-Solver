"""Tunable constants for the API layer (M61, audit recommendation #4).

Split out of `main.py`, which had grown to 3,441 lines holding
constants, caches, orchestrators, and routes together. The layering is a
clean one-way chain with no cycles:

    config  <-  caches  <-  main (orchestrators + routes)

Everything here is a VALUE, never behavior: demo hand pools, per-endpoint
cost caps, iteration budgets, board/pot defaults. Each constant keeps the
full comment explaining the real measurement behind it — those comments
are the reason this project's cost decisions survive across milestones,
so they moved with the values rather than being summarized away.

`main.py` re-imports every name here into its own namespace, which is
what lets `tests/test_api.py` keep monkeypatching `api_main.<CONST>` to
shrink pools for speed exactly as before — the split changed where these
live, not how they are read or overridden.
"""

from pathlib import Path as FilePath

from poker_solver.solver import (
    DEFAULT_FLOP_ITERATIONS,
    DEFAULT_FLOP_MULTIWAY_ITERATIONS,
    DEFAULT_FLOP_TO_RIVER_ITERATIONS,
    DEFAULT_FLOP_TURN_MULTIWAY_ITERATIONS,
)
from poker_solver.starting_hands import StartingHand, all_starting_hands

# The React app's production build (see frontend/, `npm run build`). Not
# committed to git — build it locally or in CI before serving for real.
FRONTEND_DIST_DIR = FilePath(__file__).resolve().parent.parent / "frontend" / "dist"

PREWARM_STACK_DEPTHS = (20, 40, 50, 75, 100, 150, 200)
MAX_ITERATIONS = 20_000

# The multiway preflop pool: the full 169-class canonical set, i.e. the
# real game — the same pool heads-up has always used.
#
# This REPLACED an 8-class curated pool in M67, for two independently
# sufficient reasons.
#
# 1. Coverage. The old pool meant multiway preflop advice existed for 8
#    of 169 starting hands. A 6-max request holding T7s got HTTP 200
#    with a null strategy — no advice at all for ~95% of hands, on the
#    product's own central question ("what do I do with MY hand?").
#
# 2. Correctness. That pool was 48.6% premium by combo weight, so at
#    6-max a player faced a premium ~97% of the time. M66 traced the
#    long-standing "6-max divergence" to exactly this: the solver was
#    converging correctly to a distorted game. The full pool is 2.6%
#    premium — real density — and the answers are sane (AKs's UTG open
#    folds 2.6% at 6-max, against 25.2% -> 94.5% on the old pool).
#
# The cost is real and is the reason this wasn't done sooner. Measured
# at 300 iterations, samples=200, all 169 classes: **3-max 62s, 6-max
# ~170s, 9-max ~215s** per (stack, players) spot, against ~5.6s for the
# old 8-class pool. It is paid once per spot (cached), and _prewarm_
# common_depths already warms stack_bb=100 for every table size in a
# background daemon thread, so a server that has finished warming serves
# those instantly. A non-prewarmed stack depth pays it on first request.
#
# Coverage is real, not nominal — checked rather than assumed, since a
# larger pool could plausibly have meant each class getting too few
# updates: **all 169 classes come back trained at 300 iterations.** MCCFR
# is vectorized over candidate hands (only OPPONENT hands are sampled),
# so every class receives every iteration's update.
MULTIWAY_PREFLOP_HANDS = all_starting_hands()

# Monte Carlo runouts per equity evaluation for the multiway preflop leg.
# Deliberately BELOW poker_solver's own default (200), and the reason is
# a real measured trade, not a cost cut for its own sake.
#
# With 169 classes the binding constraint is ITERATIONS, not sample
# precision: at 300 iterations the strategy is badly unconverged whatever
# the sample count (T7s folding 22.6% under the gun at 6-max, which is
# simply wrong). samples=50 costs ~3.6x less per evaluation, which buys
# ~6x the iterations for the same wall clock — and iterations are what
# this needs. Measured at 6-max, 169 classes:
#     samples=200, 300 iters  (~170s): T7s fold 22.6%, 72o fold 94.4%
#     samples=50,  3,000 iters (325s): T7s fold 69.8%, 72o fold 98.3%
#     samples=50, 10,000 iters (712s): T7s fold 86.9%, 72o fold 98.7%
# The cheaper-but-more-iterated setting is not a worse answer; it is a
# much better one.
MULTIWAY_PREFLOP_SAMPLES = 50

# One entry per supported multiway table size. `iterations` shrinks as
# player count grows — see the module docstring for why (cache-hit rate
# collapses with opponent count, not solvable by raw speed alone).
# Measured during M9 (samples=200, this hand pool, 100bb): 3-max reaches
# good convergence in seconds once cached; 9-max's per-iteration cost was
# too variable to safely budget a large count for a live endpoint (some
# iterations touch far more distinct opponent-hand combinations than
# others), so it's capped at a smaller, empirically-verified-reliable
# count (~1.5 minutes).
#
# 6-max **used to** run 30,000 iterations (~2.5 minutes), believed since
# M9 to reach "good convergence". M27 found that belief was wrong: fixing
# a real equity-fallback bug (poker_solver/equity.py's
# MultiwayEquityCache) surfaced a pre-existing MCCFR convergence
# sensitivity specific to 6-max with this small, top-heavy demo hand pool
# — some hands' fold rate at 30,000 iterations grows steadily rather than
# stabilizing (measured: AKs's UTG-open fold rate climbed from 22.8% at
# 300 iterations to 94.8% at 30,000, and kept climbing at 100,000+ —
# never leveling off). Confirmed not to be something M27's own fix
# introduced: the *pre-fix* code shows the same non-monotonic instability
# too, just biased toward over-jamming instead of over-folding (see
# CLAUDE.md's M27 entry for the full investigation, including why three
# separate placeholder-quality improvements each helped some but did not
# resolve it). No iteration count tested was fully stable — AKo's fold
# rate was already climbing noticeably by 500 iterations (38.3%), and
# even AA/KK weren't consistently stable there across different seeds —
# so there's no verified-safe *larger* number to fall back to; matching
# 9-max's own already-conservative 300 is the most defensible choice
# available, not a number this milestone specifically validated as
# sufficient. Only AA's own fold rate held up consistently across seeds
# at 300 (see tests/test_solver.py's six_max_result tests, which now
# mirror 9-max's own "only assert what's actually reliable" pattern).
# M63 re-measured all of this on current code, because the finding above
# predated M33/M34's equity fixes, M48's evaluator rewrite and M55's
# memoization — any of which might plausibly have changed it. None did:
# AKs's UTG fold rate still runs 15.6% (300) -> 48.7% (3k) -> 92.4%
# (30k), and QQ's 19.3% -> 86.2%. So 300 is NOT a cost-conservative
# number with headroom above it; it is the count at which the answer is
# still sane. Raising it makes the output actively worse.
#
# M66 FOUND THE CAUSE, and it changes what "fixing" this means. It is not
# a solver defect — it is the multiway preflop hand pool itself (then an
# 8-class curated list; see MULTIWAY_PREFLOP_HANDS). That pool was 48.6%
# premium by combo weight (AA/KK/QQ/AKs/AKo out of 8 classes). At 6-max
# the traverser faces 5 opponents drawn from it, so ~97% of the time at
# least one holds a premium — and under those conditions folding AKs
# under the gun genuinely is close to correct. More iterations converge
# harder to a correct answer to a distorted question.
#
# Proof, measured directly (M66): rerun over a pool diluted to 10.2%
# premium and AKs's UTG fold rate is 2.5% -> 1.2% -> 1.7% across
# 300 / 3k / 30k — flat at 100x this budget. A control at the same POOL
# SIZE (8 classes) but premium-light still degraded, so coarseness
# contributes as well as density; the real fix is a pool that is both
# larger and realistically weighted.
#
# M67 REPLACED THAT POOL (see MULTIWAY_PREFLOP_HANDS above): multiway
# preflop now solves all 169 classes, at a real 2.6% premium density. So
# the correctness reason for keeping these budgets SMALL is gone — over a
# realistic pool, more iterations help rather than hurt. The numbers below
# are raised accordingly, and are now bounded purely by cost.
#
# 300 iterations is NOT enough over 169 classes, which is a different
# failure from the pre-M67 one and worth stating plainly: fold rates come
# out simply wrong (T7s folding 22.6% under the gun at 6-max). Measured
# at 6-max with MULTIWAY_PREFLOP_SAMPLES=50:
#     300 iters:     T7s fold 22.6%   (wrong)
#   3,000 iters (325s): T7s fold 69.8%, 72o fold 98.3%, AA fold 0.1%
#  10,000 iters (712s): T7s fold 86.9%, 72o fold 98.7%, AA fold 0.0%
#
# Set to 3,000 as the point where the FOLD-vs-PLAY decision — the thing a
# player is actually asking — becomes plausible, at a cost that is
# tolerable behind the cache and the startup pre-warm.
#
# **An honest, deliberate limitation, not an oversight:** the split among
# the NON-fold actions (limp / raise 2.5 / jam) is still not converged at
# this budget. AA jams ~22% here where a converged solve puts it near 0.
# So treat multiway preflop output as trustworthy for "is this hand
# playable from this seat" and NOT for "which sizing". Heads-up has no
# such caveat — it solves exactly (CFR+) against a precomputed 169x169
# equity table. Closing this properly is an ARCHITECTURAL fix, not a
# budget one: multiway equity is Monte-Carlo-simulated per opponent
# tuple, which is why iterations cost what they do. See docs/milestones.md
# M67 for the profiling that establishes this and the proposed fix.
#
# M68 RAISED THESE, and they now cost LESS than M67's smaller numbers
# did: sharing board runouts across candidates (see equity.
# _simulate_equity_shared_board) made a 6-max solve ~1.95x faster, so
# 12,000 iterations costs 281s where M67's 3,000 cost 325s. Every number
# below is measured with the current code, not extrapolated — M67's
# 9-max budget was extrapolated and flagged as such, and this replaces
# it with a direct measurement.
#     3-max, 12,000 iters:  48.3s   T7s fold 93.9%, 72o 98.2%
#     6-max, 12,000 iters: 281.0s   T7s fold 87.4%, 72o 98.9%
#     9-max,  3,000 iters: 248.9s   T7s fold 12.5%  <-- see below
#
# **9-max preflop output is NOT reliable, and that is measured, not
# suspected.** T7s folding 12.5% under the gun at a 9-handed table is
# flatly wrong — it should be near 100%, and 6-max reaches 87.4%. With 8
# opponents the sampled-opponent variance is high enough that 3,000
# iterations is proportionally far less converged than the same count at
# 6-max, and the per-iteration cost (~83ms) makes the count that would
# converge unaffordable. 3,000 is kept because more is directionally
# better, not because it is enough. Treat 9-max as the least trustworthy
# cell in the whole product; 3-max and 6-max are in much better shape.
# Pinned by tests/test_solver.py's paired
# test_six_max_demo_pool_degrades_with_more_iterations and
# test_six_max_converges_with_a_realistic_pool.
# **M72: 6-max drops to 3,000, and the reason is a real constraint, not
# a cost cut.** M71 removed the CFR+ regret clamp after validating it at
# 3,000 iterations, but left the budget at 12,000 without re-measuring
# there. Without the clamp, AA's jam frequency GROWS with iterations
# (2 seeds each): 0.033 at 3,000 -> 0.149 at 6,000 -> 0.404 at 12,000.
# So the shipped 12,000 was worse than what M71 replaced. Caught by an
# end-to-end /advise check, not by any unit test.
#
# 3,000 is where 6-max is measured best on BOTH axes and is also the
# cheapest (133s vs 309s): AA jam 0.033 (reference ~0.031), T7s UTG fold
# 0.963. Do not raise it without re-measuring the jam frequency —
# `test_six_max_jam_frequency_at_the_shipped_budget` pins this.
#
# 3-max keeps 12,000 because it measured the OPPOSITE way (AA jam 0.527
# at 3,000 vs 0.120 at 12,000, 3 seeds) and costs only 48s there. The two
# table sizes genuinely disagree about the right budget; each is set from
# its own measurement rather than from a shared assumption.
MULTIWAY_TABLE_CONFIGS = {
    3: {"positions": ("BTN", "SB", "BB"), "iterations": 12_000},
    6: {"positions": ("UTG", "MP", "CO", "BTN", "SB", "BB"), "iterations": 3_000},
    # `floor_regret` (M71): every table size uses plain CFR regret
    # matching now EXCEPT 9-max. CFR+'s clamp is a ratchet under sampling
    # and dropping it is a large win at 3-max and 6-max (AA's jam
    # frequency 0.468 -> 0.120 and 0.199 -> 0.032 respectively, three
    # seeds each). But plain CFR converges more slowly, and 9-max's 3,000
    # iterations split across nine seats give each only 333 traversals —
    # too few, measured: AA's jam goes the WRONG way there (0.777 ->
    # 0.982). So 9-max keeps the clamp until its budget can support the
    # better rule. One more reason 9-max is the least trustworthy cell.
    9: {
        "positions": ("UTG", "UTG1", "MP1", "MP2", "MP3", "CO", "BTN", "SB", "BB"),
        "iterations": 3_000,
        "floor_regret": True,
    },
}

# /solve_flop's curated demo ranges — small hand-*class* sets (not
# combo lists, unlike MULTIWAY_PREFLOP_HANDS), expanded into actual
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

# /solve_flop_multiway's and /solve_flop_turn_multiway's curated demo
# pool (M37) — a real 3-max multiway flop, wiring up solve_flop_multiway/
# solve_flop_turn_multiway (M35/M36) into a live endpoint for the first
# time. One SUITED class per position (not a pair, unlike DEMO_CHAINED_
# FLOP_HERO_/VILLAIN_CLASSES above) — 4 combos each, board-legal
# expansion measured at 11 total on DEFAULT_MULTIWAY_FLOP_BOARD (one of
# MID's own combos is blocked by the board's own Jh). Shared between
# both new endpoints, matching DEMO_CHAINED_FLOP_HERO_/VILLAIN_CLASSES'
# own "one pool, multiple depths" precedent. Deliberately 3-max only —
# M35/M36 both measured pool size (not position count directly, but a
# wider table needs a wider pool to give every position real reach) as
# the dominant cost driver for this whole solving path; 6-max/9-max
# multiway POSTFLOP is unscoped, unmeasured future work, the same "prove
# 3-max first" precedent M8/M9 already established for multiway PREFLOP
# before 6-max/9-max were tackled as their own, later milestones.
DEMO_MULTIWAY_FLOP_POSITIONS = ("OOP", "MID", "IP")
DEMO_MULTIWAY_FLOP_CLASSES = {
    "OOP": {StartingHand("A", "K", suited=True): 1.0},
    "MID": {StartingHand("Q", "J", suited=True): 1.0},
    "IP": {StartingHand("T", "9", suited=True): 1.0},
}

# Matches ActionPathSolver.tsx-style default board conventions elsewhere
# in this file (DEFAULT_CHAINED_FLOP_BOARD) — used only to pick what a
# future pre-warm pass would warm; these endpoints are not pre-warmed
# today (see the module docstring's own note on why).
DEFAULT_MULTIWAY_FLOP_BOARD = "Jh7d2c"

# Real sized bet + all-in, matching FLOP_TURN_MAX_RAISES/FLOP_TURN_
# RAISE_SIZES's own choice for consistency — shared between both new
# endpoints, same as those two share one raise-sizing menu.
MULTIWAY_FLOP_MAX_RAISES = 2
MULTIWAY_FLOP_RAISE_SIZES = (2.5,)

# Measured live, at DEMO_MULTIWAY_FLOP_CLASSES' own 11-combo pool (see
# the module docstring for the full numbers this milestone's own scoping
# pass produced): solve_flop_multiway's cost is close to flat across
# iteration count (200 iters ~3.0-3.5s, 1000 iters ~3.5s, 2000 iters
# ~3.5s — the equity cache saturates quickly at this small a pool), so a
# generous cap is safe, mirroring MAX_FLOP_TURN_ITERATIONS's own
# identical "flat cost" reasoning. solve_flop_turn_multiway's cost is
# NOT flat — it scales close to linearly with iteration count instead
# (50 iters ~1.3s, 200 iters ~5.8s, 500 iters ~13.8s — every iteration
# can sample a genuinely new (terminal, card) pair, a much bigger space
# than the flop-only equity cache's own opponent-tuple space at this
# pool size), so its own cap is set far more conservatively, landing at
# the same "slow but tolerable for a live request" ~14s ceiling rather
# than following solve_flop_multiway's generous 10x-default headroom.
MAX_FLOP_MULTIWAY_ITERATIONS = 2_000
MAX_FLOP_TURN_MULTIWAY_ITERATIONS = 500

# solve_flop_to_river_multiway's own cap (M40, wiring up M39's engine
# work) — measured live at the same 11-combo pool: cheaper than solve_
# flop_turn_multiway at every iteration count compared (200 iters
# ~3.89s vs. ~5.8s; the most expensive point measured, 500 iterations,
# was ~9.54s vs. solve_flop_turn_multiway's own ~13.8s at the same
# count), the OPPOSITE of solve_flop_to_river's own 2-position finding
# (M13/M14, where the second hop was dramatically MORE expensive than
# the first — see CLAUDE.md's M39 entry for the two independent reasons
# why: build_mccfr_chance_branch's lazy, one-sampled-card-at-a-time
# design never pays build_chance_node's eager combinatorial cost, and a
# complete-river-board equity lookup needs no enumeration at all, unlike
# a turn-level lookup's own already-cheap exact enumeration). Set equal
# to solve_flop_turn_multiway's own default/cap (50/500) rather than
# solve_flop_to_river's tiny 2-position ones (20/=default, zero
# headroom) — the cost profile that justified those numbers doesn't
# hold here.
MAX_FLOP_TO_RIVER_MULTIWAY_ITERATIONS = 500

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
# M54 re-tune, after M48's ~5-6x hand-evaluator speedup. Re-measured
# with the PREFLOP leg pre-warmed so the number reflects only what this
# cap actually controls (the first sweep didn't, and produced an
# impossible "larger cap is faster" reading that gave the flaw away):
# cap=6 -> 3.47s, cap=10 -> 11.17s, cap=14 -> 22.56s. Raised 6 -> 10:
# ~67% more range fidelity, still well inside the tolerable-for-a-live-
# request bracket, with cap=14 left as measured headroom rather than
# taken now.
MAX_PATH_QUERY_CLASSES_PER_SIDE = 10
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
# M55 re-tune, and a CORRECTION to what M54 claimed here.
#
# M54 left this at 2 and asserted solve_flop_turn was "dominated by
# cfr._solve_recurse's tree traversal (531K recursive calls), not by
# hand evaluation". That was WRONG — derived from a stale pre-M48
# profile, and by misreading cProfile's CUMULATIVE time as self time.
# Re-profiled by SELF time on the current code: build_board_equity_table
# is 9.65s self / 30.52s cumulative of 41.16s total (~74%), while
# _solve_recurse's own self time is only 5.05s (~12%). Equity-table
# CONSTRUCTION dominates, not traversal.
#
# That correction is what found M55's actual lever (see chance.py's
# build_chance_node): those tables were being rebuilt identically once
# per showdown-eligible flop terminal — measured at exactly 7.00x
# redundancy (343 builds, 49 distinct inputs). Memoizing them is
# provably lossless, and re-measured here: cap=2 32.77s -> 10.18s,
# cap=3 78.84s -> 19.93s, cap=4 107.27s -> 25.04s.
#
# So M54's "no headroom here" conclusion is obsolete: cap=4 now costs
# LESS (25.04s) than cap=2 did before (32.77s). Raised 2 -> 4 — double
# the range fidelity AND faster than the old setting.
MAX_TURN_PATH_QUERY_CLASSES_PER_SIDE = 4

# /solve_river_from_path's (M46) own cost controls — capped by COMBO
# count directly, not by class the way every other path-derived
# endpoint's own cap is (MAX_PATH_QUERY_CLASSES_PER_SIDE, MAX_TURN_
# PATH_QUERY_CLASSES_PER_SIDE, MAX_MULTIWAY_PATH_QUERY_CLASSES_PER_
# POSITION). Real, measured reason: solve_flop_to_river's cost scales so
# steeply with combo-pool size that even a single CLASS (which can
# expand to up to 12 combos, e.g. an offsuit hand) is already too coarse
# a lever.
#
# Re-measured at M49, after M48's ~5-6x hand-evaluator speedup — the
# cap below was never re-derived from scratch, just re-benchmarked
# against the same real preflop line/board this endpoint's own cost
# comments have always used. Combo-pool-size scaling is no longer close
# to linear at this speed (measured: 1/side (2 total) ~14s pre-M48 vs.
# a modest few seconds now; 3/side (6 total) ~11-39s; 6/side (12 total)
# ~18-40s across repeated runs — a real, honestly-reported timing spread
# this milestone hit and re-measured twice before trusting, not a single
# cherry-picked number; 9/side (18 total) jumped to ~76-110s, a genuine
# super-linear cliff, not just cap=6's own variance). Set to 6 combos
# per side (~40s at the slower end of its own observed range) — DOUBLE
# M46's original cap, at roughly the SAME wall-clock cost M46's own
# narrower cap=3 used to cost pre-M48, landing in the same "slow but
# tolerable for a live request" bracket this project has used
# throughout. cap=9's own cliff is exactly why this wasn't pushed
# further without more real measurement first.
# M55 re-tune, on top of M49's own: the same equity-table memoization
# (build_chance_node) helps here too, and more, since a chained river
# solve builds two levels of chance nodes. Re-measured: cap=6 ~40s
# (M49's number) -> 17.18s; cap=9 -> 31.72s, still cheaper than cap=6
# cost before. Raised 6 -> 9: 50% more combo fidelity, still faster than
# the previous setting.
RIVER_PATH_QUERY_MAX_COMBOS_PER_SIDE = 9

# solve_flop_to_river's own cost still scales meaningfully with
# iteration count at this pool size (re-measured at M49's own new
# cap=6: 20 iters ~39-40s, 50 iters ~54s, 100 iters ~75-76s — twice
# independently confirmed, not a one-off). Mirrors MAX_FLOP_TO_RIVER_
# ITERATIONS' own "==default, zero headroom" discipline for the
# identical reason: cost at this scale is already at the outer edge of
# tolerable for a live request at its own default, so `river_iterations`
# can only ever request a faster, noisier result, never a slower one.
DEFAULT_RIVER_PATH_QUERY_ITERATIONS = DEFAULT_FLOP_TO_RIVER_ITERATIONS
MAX_RIVER_PATH_QUERY_ITERATIONS = DEFAULT_FLOP_TO_RIVER_ITERATIONS

# /solve_flop_multiway_from_path's (M42) own cost controls — the
# multiway analog of MAX_PATH_QUERY_CLASSES_PER_SIDE/PATH_QUERY_
# ITERATIONS, separately measured since solve_flop_multiway's own cost
# curve is far steeper than solve_flop's (M35's own finding: pool size
# is the dominant cost driver, compounded by MCCFR's opponent-sampling
# cache-miss rate). Unlike /solve_flop_from_path's preflop leg (which
# solves over the FULL 169-class pool at players=2), this endpoint's
# preflop leg USED to be restricted to a small 8-class pool whenever
# players != 2 — M67 made it the same full 169-class pool
# (MULTIWAY_PREFLOP_HANDS), so that asymmetry is gone and this cap now
# genuinely binds (see the M67 re-measurement below)
# (see _get_or_solve_preflop_raw's own docstring) — so a much smaller
# per-position cap than MAX_PATH_QUERY_CLASSES_PER_SIDE's 6 is both
# necessary (this endpoint's own steep cost curve) and sufficient
# (there are only 8 classes to rank from in the first place). Measured
# for real, at a real 3-max open/call/call path reaching a genuine
# 3-live-position flop, solve_flop_multiway's own default (200)
# iterations: cap=1 -> 18 combos, ~3.33s; cap=2 -> 35 combos, ~22.46s;
# cap=3 -> 62 combos, ~46.63s. Set to 2 — landing in the same
# "tolerable for a live request" bracket /solve_flop_from_path's own
# ~17-21s established, while keeping more range diversity than a
# single top class per position would.
# M54 re-tune: cap=2 -> 7.42s, cap=4 -> 16.80s, cap=6 -> 17.03s
# (M42 measured cap=2 at ~22.46s pre-M48, so ~3x faster). Note cap=6
# cost essentially the SAME as cap=4 there, because the preflop leg
# solved over only 8 classes, so a derived range had fewer than 6
# classes with meaningful weight and the cap never bound. M54 raised it
# to 6 anyway — free on that path — and left an explicit caveat that a
# path whose derived range IS wider "would cost more than 17.03s",
# unmeasured.
#
# M67 MADE IT BIND and measured it, since the preflop leg is now the
# full 169-class pool. On a real 6-max open/call/call path reaching a
# genuine 3-live-position flop, preflop leg pre-warmed and excluded:
# **cap=2 -> 3.1s, cap=4 -> 6.6s, cap=6 -> 11.5s.** So the caveat was
# pessimistic — a genuinely binding cap=6 costs LESS than M54's own
# non-binding 17.03s reading. (Not a like-for-like refutation: M54's
# path was 3-max-origin, this one 6-max-origin. The durable point is
# that cap=6 is comfortably inside the "tolerable for a live request"
# bracket even now that it does real work.) Left at 6.
MAX_MULTIWAY_PATH_QUERY_CLASSES_PER_POSITION = 6

# Iteration-count scaling at this cap's own 35-combo pool is NOT close
# to flat, unlike DEMO_MULTIWAY_FLOP_CLASSES' own tiny 11-combo pool
# (see MAX_FLOP_MULTIWAY_ITERATIONS' own comment) — measured, same
# path/board as above: 200 iters ~22.46s, 500 iters ~36.76s, 1000 iters
# ~48.20s, 2000 iters ~58.13s. Default kept at solve_flop_multiway's
# own default (200); cap set to 500 (~37s) rather than solve_flop_
# multiway's own generous 2000-iteration ceiling, which was tuned
# against a much smaller (11-combo) pool.
DEFAULT_MULTIWAY_PATH_QUERY_FLOP_ITERATIONS = DEFAULT_FLOP_MULTIWAY_ITERATIONS
MAX_MULTIWAY_PATH_QUERY_FLOP_ITERATIONS = 500

# /solve_turn_multiway_from_path's (M44) own class cap and iteration
# bounds — deliberately its own, not MAX_MULTIWAY_PATH_QUERY_CLASSES_
# PER_POSITION reused, mirroring MAX_TURN_PATH_QUERY_CLASSES_PER_SIDE's
# own M26 precedent (the flop-level cap doesn't automatically transfer
# to a chance-dispatched turn-level solve's steeper cost curve). Ended
# up landing on the SAME value (2) here, unlike M26's own case where the
# turn-level cap had to shrink from the flop-level one — solve_flop_
# turn_multiway's chance dispatch turned out cheap enough (M36's own
# finding: lazy, one-sampled-card-at-a-time construction, not M13's
# eager ~44x49 combinatorial cost) that the same class count stays
# affordable. Measured for real, same 3-max open/call/call path/board as
# MAX_MULTIWAY_PATH_QUERY_CLASSES_PER_POSITION's own comment, chained
# into solve_flop_turn_multiway instead of solve_flop_multiway: cap=1 ->
# 18 combos, 50 iters ~4.19s / 200 iters ~16.17s; cap=2 -> 35 combos, 50
# iters ~10.12s / 200 iters ~42.69s. Set to 2 for the same range-
# diversity reasoning as the flop-only endpoint; default iterations kept
# at solve_flop_turn_multiway's own default (50, ~10.12s at this pool),
# cap set to 200 (~42.69s) — landing in the same "slow but tolerable for
# a live request" bracket /solve_turn_from_path's own ~46s already
# established, not solve_flop_turn_multiway's own more generous 500-
# iteration ceiling (tuned against the much smaller 11-combo demo pool).
# M54 re-tune, and the biggest winner of M48's speedup by far: cap=2 ->
# 1.38s, cap=4 -> 2.32s, cap=6 -> 2.21s (M44 measured cap=2 at ~10.7s
# equivalent once its own 200-vs-50 iteration difference is accounted
# for — so roughly 8x). Same 8-class structural ceiling as the flop
# multiway cap above, and raised to 6 for the same reason: measured free
# on this path, real extra fidelity on a wider one.
#
# M67 re-measured this one too, for the same reason (the preflop leg is
# now the full 169-class pool, so the cap genuinely binds). Same 6-max
# path as the flop cap above, preflop leg pre-warmed and excluded:
# **cap=2 -> 0.6s, cap=4 -> 1.0s, cap=6 -> 1.5s.** Comfortably the
# cheapest capped path in the codebase; left at 6.
MAX_MULTIWAY_TURN_PATH_QUERY_CLASSES_PER_POSITION = 6
DEFAULT_MULTIWAY_TURN_PATH_QUERY_FLOP_ITERATIONS = DEFAULT_FLOP_TURN_MULTIWAY_ITERATIONS
MAX_MULTIWAY_TURN_PATH_QUERY_FLOP_ITERATIONS = 200
