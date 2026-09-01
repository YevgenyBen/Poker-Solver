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

# M76: which stack depths to pre-warm for the MULTIWAY tables. Separate
# from PREWARM_STACK_DEPTHS above because the cost is different by two
# orders of magnitude: a heads-up preflop solve is ~3s, a 6-max one over
# the full 169-class pool is ~90-130s.
#
# Before this, multiway pre-warm covered stack_bb=100 only, which the
# 2026-08-22 diagnostic measured as a real usability failure rather than
# a theoretical one: a player sitting at a 30bb table waited **66
# seconds** for their first answer, and a 9-max player 126s. That is not
# a product a person can use with a clock running.
#
# Three depths, chosen for what people actually sit behind: 100bb (the
# standard cash buy-in), 50bb (a common short-stack cash seat), and 20bb
# (tournament/push-fold territory). Costs roughly 15 minutes of
# background work at startup across the three table sizes — paid once, in
# a daemon thread, while the server already serves everything else.
# Depths outside this list still work; they just pay the solve.
MULTIWAY_PREWARM_STACK_DEPTHS = (100.0, 50.0, 20.0)
# The ceiling on a client-supplied `iterations` for the heads-up preflop
# solve — the one endpoint that exposes the knob at all (multiway ignores
# it outright, per MULTIWAY_TABLE_CONFIGS' fixed-menu discipline).
#
# M124 (D5) gave this its measurement; it was the only constant in this
# file sitting with no justification of its own. Cost is close to linear
# in iterations for a 169-class heads-up solve:
#
#     1,000 iters ->  2.8s
#     5,000 iters -> 12.1s
#    20,000 iters -> 50.0s
#
# So the ceiling is a ~50s worst case, which is the same "slow but
# tolerable for a live request" bracket MAX_FLOP_TURN_ITERATIONS and
# MAX_FLOP_MULTIWAY_ITERATIONS were both set against. It exists to stop a
# client requesting an unbounded solve, not to mark a convergence limit —
# heads-up CFR+ is converged long before this.
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
# precision. **Re-measured properly in M99** — the original evidence for
# this was weak and reached the right answer by luck. It compared
# `samples=200 @ 300 iters` (~170s) against `samples=50 @ 3,000 iters`
# (325s): two variables at once, two very different costs, and only fold
# rates reported. M98 then found that the constant it justified was also
# implicated in a real defect, which is what prompted the re-measurement.
#
# M99 held wall clock roughly fixed instead, 9 seeds per arm, reading
# T7s's under-the-gun fold rate (a marginal hand that should fold ~always
# — trash like J4o/95o folds ~0.999 in every arm and cannot discriminate):
#
#     arm              T7s fold +/- SE   worst   below 0.8   AA jam   cost
#     50  x 3,000      0.866 +/- 0.051   0.486       2/9      0.116    98s
#     200 x   750      0.485 +/- 0.099   0.061       8/9      0.832   144s
#     400 x   375      0.419 +/- 0.111   0.000       7/9      0.991   149s
#
# Iterations dominate, and not marginally: starve them and one seed in
# nine folds T7s **0% of the time** under the gun while jamming AA 99%.
# The shipped arm is best on every measure AND the cheapest — note the
# arms are NOT equal-cost despite holding samples*iterations constant,
# because per-iteration tree/NumPy work does not scale with samples.
#
# **What this does NOT fix, stated plainly:** even at the shipped setting
# 2 seeds in 9 fold T7s below 0.80, one at 0.486. That instability is
# real, it is what the +/-55bb frozen equity error (M98) buys you, and
# neither knob removes it at this budget — more samples at fixed cost
# makes it much worse. It needs more total budget or structural work, not
# retuning.
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
# playable from this seat" and NOT for "which sizing".
#
# **M98 found the cause, and it is NOT the budget.** "Not converged at
# this budget" implied a bigger budget would fix it. It does not: at
# 12,000 iterations and 400 equity samples — the most converged, least
# noisy setting measured — AA jams 0.649 and KK 0.709. More of both
# converges ONTO the jam, because jamming is what this model actually
# prefers. Every showdown terminal is priced `equity * pot - invested`
# (`cfr._mccfr_terminal_value`), so an all-in is priced correctly (it
# really does end at showdown) while every smaller bet is priced as if
# the hand ended immediately, discarding the postflop game that is most
# of a raise's value. The error grows with opponent count, because more
# opponents means more chance the accurately-priced all-in gets called.
# Heads-up does NOT show this, and why is an open question — M98 offered
# a cancellation argument ("a jam there just wins the blinds") that it
# never measured and that does not hold up: a jam's value depends on
# villain's calling frequency against the whole shoving range, not on one
# hand's. Corrected in M99 rather than left as a plausible story.
#
# What sample count DOES control is the INSTABILITY: a 50-sample equity
# estimate carries an error of +/-55bb of EV in a six-way 100bb pot,
# frozen per cache key, which is why the jam frequency swings with the
# seed. Fixing sizing needs postflop continuation value at preflop
# terminals — see SIZING_CAVEAT_REASON, which is what users are told
# meanwhile. Heads-up has no
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
    # M157: 12,000 iterations and PLAIN CFR, replacing 3,000 with the
    # clamp. M71 kept the clamp here "until its budget can support the
    # better rule" — 3,000 iterations gave each of nine seats only 333
    # traversals, where plain CFR went the wrong way. At 12,000 it is
    # 1,333 per seat and the better rule wins decisively. Three seeds,
    # every one better on every measure, with no overlap between arms:
    #
    #   arm                T7s fold (3 seeds)          AA jam     72o fold
    #   3,000 + clamp      .1522 / .0678 / .1450       .81-.85    .973-.982
    #   12,000 plain       .8628 / .4508 / .8783       .06-.17    1.0000
    #
    # T7s reaches a mean 0.731 against 6-max's documented 0.874, where
    # the shipped arm managed 0.122. **config.py's own claim that "the
    # per-iteration cost makes the count that would converge
    # unaffordable" was an INFERENCE, never measured** — the 12.5% figure
    # behind it was taken at one budget and nobody ran a higher one.
    # Cost is 3.1x (139-168s -> 473-580s), paid once per (stack, players)
    # and pre-warmed at startup.
    #
    # 9-max stays flagged low-confidence: the T7s spread across seeds is
    # 0.43, so this is materially better advice, not a converged solve.
    9: {
        "positions": ("UTG", "UTG1", "MP1", "MP2", "MP3", "CO", "BTN", "SB", "BB"),
        "iterations": 12_000,
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
# M131 raised this 10 -> 26, and cut PATH_QUERY_EQUITY_SAMPLES and
# PATH_QUERY_ITERATIONS to pay for it. The three move together and the
# reason is one measurement — see PATH_QUERY_EQUITY_SAMPLES below.
# M172: raised 26 -> 100, and this is the first widening in this
# project's history that IMPROVED accuracy rather than redistributing it.
#
# Why every previous attempt failed, and why this one does not. The cap
# is a coverage problem, and coverage was never measured until now: the
# derived opponent range has all 169 classes nonzero, and the top 26
# carry a median of only **28% of its mass** (worst 15%). The solve was
# reasoning about a quarter of what the opponent can hold.
#
#   cap   mass kept   error vs the uncapped reference   solve cost
#    26        28%                            0.2005         0.33s
#    44        38%                            0.2169         0.79s
#    60        66%                            0.2685         1.38s
#   100        95%                            **0.1065**     4.03s
#   140        99%                            0.0956         6.63s
#
# Error gets WORSE from 26 to 60 - M141's conservation law, faithfully -
# and then halves at 100. Every earlier widening (M137 to 60, M141 to 44)
# stopped inside the redistribution regime, where all that can happen is
# error moving between hand types. M137's "width stops paying at 26" was
# true of what it tested and false as a general claim.
#
# Measured on 12 flop spots drawn from real play against the UNCAPPED
# 169-class solve at 200 samples / 2,500 iterations (~65s per solve, run
# twice per spot, drifting references discarded). Better on 8 of 12
# spots, worse on 3; spots over 0.10 error fall from 4/12 to 2/12; worst
# case 0.9904 -> 0.5924.
#
# **Conservation has not been repealed, only outweighed**: strong hands
# get slightly worse (0.0054 -> 0.0335, both far under 0.10) while
# middling and weak hands improve several-fold.
#
# FLOP ONLY. The turn and river have their own caps
# (MAX_TURN_PATH_QUERY_CLASSES_PER_SIDE,
# RIVER_PATH_QUERY_MAX_COMBOS_PER_SIDE) and their own cost curves; this
# result was measured on flop spots and must not be assumed to transfer -
# M168 is what happens when a flop measurement is applied to the turn.
# M190 raised this 100 -> 140, reversing M180's rejection — because M180
# adjudicated it on the WRONG METRIC.
#
# M180 measured cap 140 against cap 100 on pooled FREQUENCY error and
# found 0.97 sigma, with more spots worse than better, and kept 100.
# M183 then measured frequency distance to be a poor proxy for what the
# advice costs, and M189 that 74% of the cost sits in 12% of decisions.
# So M180's verdict was reached with a metric that does not track money,
# over a population where 52% of decisions cost under 0.01 bb.
#
# Re-scored on EV LOSS over the 297 costly-band spots (facing a bet,
# hand strength 0.55-0.90) that carry 79% of all facing cost:
#
#   arm       mean |loss|   paired delta      sigma
#   shipped        1.8740             --         --
#   cap 140        1.0489   -0.8250 +/- 0.1871   4.41   <- adopted
#   iters 1000     1.9651   +0.0911 +/- 0.0632   1.44
#
# **44% less cost, 4.41 sigma, better on 124 spots and worse on 62** —
# the same change frequency error rejected at 0.97.
#
# The confound was controlled rather than assumed: cap 140 sits closer to
# the 169-class reference than cap 100 does, so it could score better by
# resembling it. Two checks. (a) The `iters 1000` arm ALSO moves toward
# the reference (250 -> 1000 against its 1500) and gains nothing, which a
# pure-similarity effect could not produce. (b) The reference is
# converged in iterations: 1500 vs 3000 moves it a mean of **0.0508 bb**,
# **2.7%** of the 1.87 bb effect being measured.
#
# Cost: 1.06s -> 1.54s isolated (1.45x), flop median ~1.53s -> ~2.2s.
MAX_PATH_QUERY_CLASSES_PER_SIDE = 140
MAX_PATH_LENGTH = 20
# Flop-stage iterations, fixed — not exposed, unlike the preflop-stage
# iterations request field below. This part of the pipeline sits behind
# query_strategy's canonical-library abstraction, where a client-
# varying value would be silently ignored on a cache hit — the exact
# bug class /solve_flop_cached's own design principle already guards
# against; the preflop-stage solve, by contrast, is a plain per-request
# cache dict (see _get_or_solve_preflop_raw), so exposing *its* own
# iterations is exactly as safe as /solve/{stack_bb} already relies on.
# M131: 500, not DEFAULT_FLOP_ITERATIONS (1,000). Halved to help fund the
# wider range above; see PATH_QUERY_EQUITY_SAMPLES for the frontier this
# point was chosen from.
# M158. Refinement iterations when a flop request can warm-start from an
# earlier solve of the same canonical spot, instead of the 500 a cold
# solve runs.
#
# **Why this is the highest-value change for a player at a table.** M155
# measured flop latency at 16.8s median and 64.5s at p90 on random
# boards. A player has 15-30 seconds to act, so a tenth of decisions
# could not be answered before they had to be made. The CFR solve is 86%
# of that cost and 71 of 73 flop requests paid it COLD - because hero's
# combo is force-included before the cap, putting hero's class in the
# cache key (M76).
#
# M155 also measured that hero's inclusion moves the solve LESS than
# changing the equity seed does (p90 0.002-0.107 against 0.024-0.112), so
# a cached hero-free solve of the same canonical spot is a legitimate
# starting point. Measured across three boards, 50 refinement iterations
# from a grafted start against a full 500-iteration cold solve:
#
#     board     cold    warm   speedup   hero row delta
#     Js6cKs    8.0s    0.8s     9.7x           0.0011
#     KcKsQd    5.7s    0.6s     9.7x           0.0010
#     9dAd5s    7.6s    0.7s    11.4x           0.0155
#
# The worst delta, 0.0155, is well inside the seed-to-seed noise above -
# the warm answer is not distinguishable from the cold one by anything
# this solver can resolve. 25 iterations was also measured and is worse
# where it matters (0.0898 on the same board), so 50 is the floor rather
# than the cheapest setting that looked fine.
PATH_QUERY_WARM_ITERATIONS = 50

# M172: lowered 500 -> 250 alongside the cap rise. Once the range is
# actually covered, iterations stop mattering much - cap 100 scores
# 0.1065 at 500 iterations and 0.1090 at 250, a difference well inside
# the noise - so half of them are bought back to offset the cap's cost.
# Coverage is doing the work, which is what the mass measurement
# predicted and the reason M131's three-way budget split needed
# revisiting rather than rebalancing.
PATH_QUERY_ITERATIONS = 250

# Fixed server-side constants, not query params — same reasoning
# DEMO_FLOP_HERO_/VILLAIN_CLASSES already establish (letting a client
# control tree size/range directly is an unbounded-cost door). Different
# max_raises per endpoint: solve_flop_to_river's extra chance-node hop
# is expensive enough (see the module docstring's per-iteration cost
# finding) that it needs a shallower tree than solve_flop_turn to stay
# in a tolerable-for-a-live-request budget.
# M144/F40. **These two lines decide how much of poker the postflop
# advice can even express, and one of them empties the river.** Measured
# through /advise at production settings, the bet sizes actually offered
# at a street's opening decision are:
#
#     flop   call_or_check, raise:12.50, raise:15.00, raise:11.00, all_in
#     turn   call_or_check, raise:12.50, all_in
#     river  call_or_check, all_in
#
# So a player asking how to play the river can only be told to check/call
# or to move in for 97.5bb. `all_in: 0.11` there does NOT mean shoving
# beat betting half the pot — half the pot was never a legal action in
# the tree. The cost rationale below is about max_raises; the empty
# river SIZES were never separately justified.
#
# Surfaced to the user rather than silently fixed: widening the river
# tree is exactly the cost this endpoint's own budget notes say it cannot
# afford, and inventing a size without measuring it would be worse than
# saying what was modelled. `modelled_bet_sizes` and
# BET_SIZING_COVERAGE_NOTE report it per response, derived from the
# actions the tree really offered rather than from these constants, so
# they stay true if these change.
# M170: the turn now offers a SIZED RE-RAISE. Until here it had one raise
# size at a two-raise cap, so facing a bet the only aggressive action left
# was a ~97.5bb shove - a hand that wanted to raise a third of the pot had
# no such button, and the solver put weight on the only one there was
# (F38; M156 measured middle pair shoving 1.000).
#
# M156 BUILT this exact change and declined it, on two premises that have
# both since expired: "latency is already the top complaint" and
# "adjudicating needs a converged TURN reference" that did not exist.
# M162/M163 fixed the latency; M168 built the turn reference and showed it
# holds still.
#
# Measured on 14 turn spots drawn from real play, against a wide-range
# reference on the richer tree, scoring how often the advice COMMITS THE
# WHOLE STACK facing a bet - F38's actual symptom:
#
#   mean error   shipped 0.1471   richer 0.1069
#   better on    shipped 2 of 14  richer 12 of 14
#
# And the cost premise inverted too. M156 measured 1.5x; the solver
# changed underneath it, and the same comparison now measures
# **1.43s -> 1.62s, 1.13x**.
FLOP_TURN_MAX_RAISES = 3
FLOP_TURN_RAISE_SIZES = (2.5, 2.0)
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
# M173: the turn is solved as a STANDALONE street rather than chained
# from the flop, and this is why.
#
# The turn was the worst-covered decision in the product. Chaining
# flop->turn through chance nodes makes cost explode with range width, so
# the cap sat at 4 classes - which keeps a **median 4.6% of the
# opponent's range mass** (worst 1.8%), against the flop's 95% after
# M172. It was simultaneously the slowest decision (8.19s median across
# ten games), 57.7% of all postflop advice, and never validated.
#
# Widening the chain is not available: cap 8 costs 3.3x, cap 14 8.8x, and
# cap 26 **40x (66 seconds)**. Coverage is unreachable by that route.
#
# `solve_flop` already solves ONE street with remaining runouts averaged
# at the terminal, and nothing in it assumes three board cards - a
# four-card board is resolved EXACTLY by build_board_equity_table
# (remaining_needed == 1, M154). Solving the turn that way is **47x
# cheaper at equal coverage** (cap 26: 1.40s standalone vs 66.46s
# chained).
#
# Measured on 10 turn spots from real play against a full-range
# standalone reference, arms at 250 iterations:
#
#   coverage   mean err   median   over 0.10   solve
#   cap 4          0.3601   0.3274      7/10    0.07s   <- shipped
#   cap 14         0.1836   0.0427      4/10    0.33s
#   cap 26         0.1361   0.0399      3/10    0.28s   <- adopted
#   cap 44         0.4102   0.2474      5/10    0.71s
#   cap 60         0.2492   0.0311      4/10    1.24s
#
# Non-monotone past 26 - M141's conservation law, not the flop's clean
# threshold - but the shipped setting is the worst arm tested. Against
# it, cap 26 standalone is **2.6x more accurate and 6x cheaper**.
#
# **The unresolved part, stated rather than buried.** The reference is a
# standalone full-range solve, so this measures "does standalone converge
# as coverage rises", NOT "is standalone right versus chained". That
# comparison cannot be run: at the only coverage where chained is
# affordable (cap 14) both arms are unstable, and they agreed on 6 of 8
# spots there while disagreeing on 2 in OPPOSITE directions - which reads
# as noise, not as the chain carrying information. What standalone gives
# up is the flop betting round's influence on the turn strategy.
#
# `TURN_SOLVE_STANDALONE = False` restores the chained path exactly. The
# flag exists because the chain question is genuinely open, not as a
# migration aid.
TURN_SOLVE_STANDALONE = True
# M179 raised this from 26. M173 chose 26 from a frontier that tested
# 4/14/26/44/60 and stopped there — it never tested the regime that
# actually fixed the flop (M172: error ROSE from cap 26 to 60, then
# HALVED at 100). The latency argument that closed that door is gone: a
# turn solve was 1.67s when 26 was chosen and is 0.09s after M176.
#
# 56 spots from real play, FOUR cells — {opening, facing a bet} x {strong,
# weak} — each against a full-range 169-class reference built at the
# street's own opening pot and solved twice:
#
#   cap  aggression   fold   worst fold   solve (production path)
#    26      0.1721  0.1034       0.9608    0.09s   <- was shipped
#    60      0.1587  0.0773       0.9628    0.16s
#   100      0.1281  0.0552       0.7591    0.31s
#   140      0.0999  0.0309       0.5897    0.68s
#
# **Adopted on separability, not on the mean.** Paired against cap 26,
# only 140 clears two standard errors on aggression (-0.0722 +/- 0.0296,
# 2.4 sigma, 33 spots better / 19 worse). Cap 100's aggression gain does
# NOT (1.6 sigma) — M141 and M166 were both nearly adopted on exactly
# that kind of non-separable mean.
#
# **The FOLD axis is where it earns its cost, and every step is
# separable there**: cap 26 -> 140 is -0.0725 +/- 0.0253 (2.9 sigma) with
# **17 spots better and 2 worse**, and the worst case falls 0.9608 ->
# 0.5897. That is F38's axis — a fold error near 1.0 means the advice
# folds where a fuller solve calls, essentially always.
#
# **Certification is still REFUSED at every cap** (12-14 of 28
# strong-band spots over 0.10 at 26/60/100/140). The turn's refusal is
# STRUCTURAL, not a coverage budget — which retires the hypothesis that
# it merely needed the flop's setting.
#
# Checked for stability rather than assumed: at n=28 the four cap means
# were 0.1633 / 0.1429 / 0.1239 / 0.1036 and at n=56 they are 0.1721 /
# 0.1587 / 0.1281 / 0.0999 — the ordering and magnitudes held, which is
# what M166's split failed to do when its sample grew.
TURN_STANDALONE_CLASSES_PER_SIDE = 140

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
# M174: the river solved as a STANDALONE street, the same treatment M173
# gave the turn, one street further.
#
# The river carries the tightest budget in the product - 9 COMBOS per
# side, against the flop's 100 classes and the turn's 26 - and is the
# only street that models no bet size at all
# (FLOP_TO_RIVER_RAISE_SIZES = () at max_raises 1, so check-or-shove is
# the whole menu, F40).
#
# Both limitations have ONE cause: the river is the third leg of a
# chained flop->turn->river solve, and `solve_flop_to_river` takes ONE
# `raise_sizes` for all three streets. Giving the river a real size menu
# widens the flop and turn too, which is the cost that forced the 9-combo
# budget in the first place.
#
# Solved on its own the river should be the CHEAPEST street, not the most
# constrained: the board is COMPLETE, so `build_board_equity_table` takes
# its `remaining_needed == 0` branch and equity is EXACT - a direct
# showdown comparison, no Monte Carlo, no `equity_samples` at all (M154).
#
# Measured as an interleaved A/B through /advise on 12 real river spots,
# one flag apart, each scored against a full-range reference built at that
# request's OWN pot and stack:
#
#   arm                        strong    weak     ALL   over .10   latency
#   chained (was shipped)      0.2550  0.1347  0.1948       7/12    12.18s
#   standalone cap26 + sizes   0.1214  0.0038  0.0626       3/12     0.65s
#
# 3.1x more accurate and 19x faster, better on 10 of 12 spots. The defect
# it repairs is a real one: the chained river recommended committing the
# whole stack into a 2-5bb pot on 4 of 12 spots at 27-58% frequency —
# 6hAc shoved 17.5bb into a 5bb pot 53% of the time where the correct
# play is to check 0.9999.
#
# CAUTION on the arm table above: an earlier version of this measurement
# used solve_flop at cap 3 as a stand-in for the shipped river and it is
# NOT one (shipped is a chained 3-street solve at 9 combos / ~20
# iterations). The proxy disagreed with production on 2 of 3 spots. Every
# number here comes from /advise itself.
RIVER_SOLVE_STANDALONE = True
# M190 raised this 26 -> 140. The river was the largest single win in the
# costly-band study: **3.4573 -> 1.5845 bb**, more than halved, in a cell
# carrying 24.6% of all cost at 7.5% of decisions.
#
# M174 chose 26 from a frequency-error frontier where 26 was the best arm
# tested and 44/60 were worse — a real measurement of the wrong quantity.
# Nothing above 60 was tried, and the axis that matters was never scored.
#
# Cost: 0.07s -> 0.63s isolated (8.3x), river median ~0.11s -> ~0.67s —
# still the second-cheapest street after preflop.
RIVER_STANDALONE_CLASSES_PER_SIDE = 140
# The river's own size menu, which only a standalone solve can set
# without widening the flop and turn. M151 measured that ONE normal size
# changes the ACTION on the river, both ways - a top pair went from
# checking 0.9941 to checking 0.6449, and nine-high went from jamming
# ~0.988 to betting a third of the pot - but measured it inside the
# chained tree, where affording it was the blocker.
# Measured through /advise against a reference built at each request's own
# pot and stack: adding these is a WASH on accuracy, not an improvement —
# paired delta +0.0093 +/- 0.0181 (sem), better on 3 spots, worse on 2,
# tied on 7. They are adopted because they close F40 (the river could not
# answer "how much should I bet") at no measurable accuracy cost and
# +0.15s, NOT because they measured better. Coverage does the work here.
RIVER_STANDALONE_RAISE_SIZES = (2.5, 2.0)
RIVER_STANDALONE_MAX_RAISES = 3

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
# bracket even now that it does real work.)
#
# **M76 raised this to 8, and corrects M67's 11.5s figure.** Two changes
# since then made the old number stale: M75 now SOLVES on-demand chance
# branches rather than returning them untrained, and M76 keys the
# path-query caches on hero's class so each class solves fresh. Both are
# correctness fixes and both cost time. Re-measured on the same 6-max
# 3-live-player line, preflop leg warm:
#     cap=6   flop 39.7s  turn  9.3s   132 combos, 51/44 trained
#     cap=8   flop 45.0s  turn 10.4s   151 combos, 63/56 trained
#     cap=10  flop 51.1s  turn 11.8s   175 combos, 75/65 trained
# The curve has no knee — cost and fidelity scale together — so this is a
# judgement, not an optimum. 8 buys 14% more combos and 43% more actually
# TRAINED hands for 13% more time, while keeping a cold flop under ~45s.
# 10 is measured and available if the latency budget ever grows.
MAX_MULTIWAY_PATH_QUERY_CLASSES_PER_POSITION = 8

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
# cheapest capped path in the codebase.
#
# M76 raised it to 8 alongside the flop cap, for the same reason and from
# the same measurement (turn: 9.3s at cap=6, 10.4s at 8, 11.8s at 10).
# It stays the cheapest capped path by a wide margin.
MAX_MULTIWAY_TURN_PATH_QUERY_CLASSES_PER_POSITION = 8

# M75: how many MCCFR iterations to spend SOLVING an on-demand chance
# branch (see solver.ensure_mccfr_chance_branch).
#
# Without this, multiway turn and river advice was uniform and untrained
# — not occasionally, but always. Measured through /advise at production
# settings: a real 6-max turn node reported **0 of 132 combos trained**,
# and the river node the same, every strategy exactly 1/num_actions.
# Confirmed long-standing, not a recent regression: the same probe on
# pre-M66 code gives 0 of 53.
#
# The cause is structural. MCCFR samples ONE next card per terminal per
# iteration, so the specific card a client asks about is almost never one
# the solve happened to sample at the specific terminal their line
# reaches — and the client picks that card, not the solver. Heads-up
# never had this because the exact solver's build_chance_node enumerates
# every card eagerly. `ensure_mccfr_chance_branch` built the missing
# branch correctly but left it unsolved, which its own docstring called
# "a live endpoint's own necessary cost tradeoff, not a bug" — accurate
# about the mechanism, too optimistic about the frequency.
#
# 100 measured against 0 and 400, at production settings, on a real 6-max
# 3-live-player line (marginal cost, preflop leg already warm):
#     train=0    turn 0/132 trained, hero untrained
#     train=100  turn 44/132, river 44/132, hero TRAINED, ~7-9s
#     train=400  turn 44/132, river 27/132, hero trained, ~14-17s
# 400 buys no extra coverage for double the cost, so 100 it is. Coverage
# stops at ~44/132 because MCCFR samples paths — not every combo reaches
# every node — and `trained` reports that honestly per combo rather than
# pretending otherwise.
MULTIWAY_BRANCH_TRAIN_ITERATIONS = 100


# M76: table sizes whose solver is known NOT to converge, and the reason
# a caller is given. Everything absent from this map is "high".
#
# 9-max earns its place by measurement, not suspicion. At the shipped
# 3,000-iteration budget its opening advice is wrong in ways a player
# would immediately notice: T7s under the gun comes back with *call* as
# its top action where correct play folds it near 100% of the time, and
# AA comes back as a 100bb shove. Both are reported `trained: true`,
# because they ARE real solves — they are simply solves of a problem the
# sampler has not had enough traversals to get right. Iterations divide
# among seats (`traverser = positions[iteration % len(positions)]`), so
# nine positions each receive a third of what six do at the same budget,
# and the gap does not close with more: T7s's fold rate measured 0.117 at
# 3,000 iterations and only 0.301 at 9,000, against 6-max's 0.94.
LOW_CONFIDENCE_TABLE_SIZES = {
    9: (
        "9-max preflop is the least converged table size: iterations divide among "
        "nine seats, so each gets a third of what 6-max gives. It is much better "
        "than it was — a hand like T7s under the gun now folds about 73% of the "
        "time on average where it used to fold 12%, and the answer for a premium "
        "no longer swings wildly — but it still varies with the solver's random "
        "seed, by up to 0.43 on that same hand. Treat it as a strong hint rather "
        "than GTO, and lean on the fold-or-play call rather than the exact "
        "frequency."
    ),
}

# M98. Multiway preflop answers TWO questions at once and they are not
# equally reliable — this module has said so since M67 in the
# MULTIWAY_TABLE_CONFIGS comment ("trustworthy for 'is this hand playable
# from this seat' and NOT for 'which sizing'"), and nothing ever told a
# user. `solver_confidence` covers only 9-max, so a 6-max player asking
# whether to raise or shove got a confident answer the project's own
# config documents as unconverged.
#
# Kept separate from `solver_confidence` deliberately: marking the whole
# response low would be its own kind of wrong, because the fold-vs-play
# decision at 3-max and 6-max is the part that IS converged and is what
# most players are actually asking.
#
# Scoped to preflop — and that scoping is NOT fully justified yet, which
# is stated here rather than left to read as settled.
#
# The defect M98 found is in TERMINAL PRICING
# (`cfr._mccfr_terminal_value` awards the pot by raw equity), so it
# applies wherever a tree scores a called bet as an immediate showdown,
# not only preflop. Severity should scale with how many streets of
# betting go unmodelled: preflop three, a flop-only tree two, a turn tree
# one, and a river tree none — on the river `equity * pot` is exact. That
# predicts multiway FLOP sizing carries a weaker version of the same
# problem.
#
# **M99 measured it, and the prediction holds.** Identical board, ranges,
# pot, stack, sizes and cap; the only variable is how much future betting
# the tree can see:
#
#     tree              all-in   check   mixedness   nodes
#     flop only         0.5652   0.4348    0.687        4
#     + real turn       0.5099   0.4901    0.601      200
#     + turn and river  0.4635   0.5365    0.592    9,608
#
# Each street the tree gains moves ~5 percentage points off the all-in -
# 10.2pp monotone end to end. All three use the exact two-player solver
# and are deterministic, so that is a real difference, not sampling
# noise, and the monotonicity is what a coincidence would not produce.
#
# It is still NOT flagged at the flop, and that is a judgement call worth
# stating: 5.5pp is an order of magnitude smaller than the preflop
# distortion (AA 0.649 vs a ~0.03 reference), it was measured on ONE spot
# at SPR 1.5, and a caveat attached to every postflop response would
# devalue the preflop one that marks a genuinely unusable axis. Revisit
# if it is measured across more spots and turns out larger. Postflop
# responses do carry `trained`/`range_confidence`, but those describe the
# RANGE, not the terminal pricing, so they do not cover this.
#
# Every multiway table size is listed — the defect is a property of the
# sampled solve, not of any one seat count. Measured: at
# 6-max AA's all-in frequency swings 0.03-0.92 across seeds and iteration
# budgets where a converged solve puts it near 0.03 (M72-M74, M97), and
# the equity estimates the choice rests on carry an error of +/-55bb of
# EV in a six-way 100bb pot (M98).
SIZING_CAVEAT_TABLE_SIZES = {
    3: True,
    6: True,
    9: True,
}
# **The "trust the fold-vs-play call" half was too strong (M110).** It
# was written when only the SIZING axis had been measured against a
# reference. A random-deal simulation then measured the implied OPENING
# RANGE per seat, and multiway preflop has a positional defect of its
# own:
#
#     6-max opening frequency by seat (combo-weighted)
#     budget    UTG     MP      CO      BTN     SB      GTO shape
#      3,000    0.281   0.319   0.316   0.384   0.498   ~0.15 rising to ~0.45
#     12,000    0.176   0.171   0.174   0.159   0.806
#
# At the shipped 3,000 the gradient is compressed — six points of
# widening across four seats where GTO spans about thirty. At 12,000 the
# BUTTON OPENS TIGHTER THAN UNDER THE GUN (0.159 vs 0.176), on both seeds
# tested, while the small blind opens 0.72-0.81 against a GTO ~0.45.
#
# The inversion needs no published chart to condemn: later position must
# open wider. So the honest claim is narrower than "trust fold-vs-play" —
# individual hands are classified sensibly (40 random deals produced zero
# categorical violations: premiums never folded, trash folded), but the
# RANGE WIDTH per seat does not reproduce positional advantage.
# M123 corrected the second half of this text. It used to tell users
# "at 6-max the button has measured TIGHTER than under the gun" — which
# is M110's claim, and **M111 withdrew it in the same milestone that
# sharpened the finding**: the 1.7pp gap it rested on is smaller than
# the 2.8pp CO varies between seeds. What M111 actually established is
# stronger and simpler — among the non-blind seats position is not
# learned AT ALL, with fold mass flat at 0.82-0.84 across UTG/MP/CO/BTN.
# A user-facing string is the last place a retracted measurement should
# survive, so this now says the thing that was measured.
SIZING_CAVEAT_REASON = (
    "Multiway preflop is unreliable for which sizing to use: the split among the "
    "non-fold actions (limp / raise / all-in) moves with the random seed — at 6-max, "
    "AA's all-in frequency has measured anywhere from 0.03 to 0.92 where a converged "
    "solve puts it near 0.03. The fold-vs-play call is sounder but is NOT a positional "
    "range chart: individual hands are classified sensibly (premiums are never folded, "
    "trash is), while the opening range does not widen with position at all — at 6-max "
    "the fold frequency is flat across UTG, MP, CO and BTN, where real GTO play widens "
    "from roughly 15% of hands under the gun to roughly 45% on the button. Treat this "
    "as a strong hint about whether a hand is playable, not as a guide to how position "
    "should change your range."
)
# The flop-leg budget for /solve_turn_multiway_from_path — the multiway
# TURN sibling of DEFAULT/MAX_MULTIWAY_PATH_QUERY_FLOP_ITERATIONS above.
# A turn query solves the flop first and then chains a sampled chance
# branch onto it, so this governs the first of those two legs.
#
# **These are the only two constants in this file with no measurement of
# their own, and that is a real gap, not a style nit (found by M101's
# audit).** Every other constant here carries the numbers that chose it;
# these were added alongside the route and inherited their values by
# analogy: the default from `solve_flop_turn_multiway`'s own default, and
# the cap set to 200 rather than the flop sibling's 500 on the reasoning
# that a turn query pays for a chance branch on top of the flop solve, so
# it cannot afford the same flop budget.
#
# That reasoning is sound and it is still not a measurement. What IS
# measured is the endpoint as a whole (~1.5s at the shipped caps, see
# MAX_MULTIWAY_TURN_PATH_QUERY_CLASSES_PER_POSITION), which bounds the
# total but says nothing about where the cap should sit. Anyone raising
# this should measure the flop leg directly first, the way the flop
# sibling's comment does.
DEFAULT_MULTIWAY_TURN_PATH_QUERY_FLOP_ITERATIONS = DEFAULT_FLOP_TURN_MULTIWAY_ITERATIONS
MAX_MULTIWAY_TURN_PATH_QUERY_FLOP_ITERATIONS = 200


# M124 (D1). The bucket width for the multiway preflop solve cache.
#
# That solve is the most expensive thing in the product — measured cold
# at 66s (6-max) and 93s (9-max) — and was keyed on `round(stack_bb)`,
# so a client walking depths paid it once per integer bb while the
# startup pre-warm covered three depths of the ~200 plausible ones.
#
# 5bb matches canonicalize.DEFAULT_STACK_BUCKET_BB, and the same FLOOR
# rule applies for the same reason (M95): the solve runs at the bucketed
# depth, so `canonical <= real` must hold or the advice names bets the
# player cannot afford.
#
# Justified by a control, not by analogy to the postflop library.
# Preflop IS depth-sensitive where postflop board texture is not, so
# adopting this needed evidence that the substitution error is small
# **relative to the noise already present**. Measured at 3-max over all
# 169 classes' fold frequency:
#
#     depth   same depth, seed 1 vs 2       5bb floor-bucket, same seed
#     24bb    mean .050  max .778  8 flips  mean .051  max .894  8 flips
#     99bb    mean .053  max .652  12 flips mean .046  max .453  10 flips
#
# Re-running the identical solve with a different seed moves the
# strategy as much as bucketing 4bb away does — more, at 99bb. The
# error is inside the solver's own run-to-run variance.
#
# Two things follow. Widening this bucket needs that control re-run, not
# an argument. And the control says something uncomfortable in its own
# right: 8-12 of 169 hands cross the fold/play line between two runs
# that differ only in seed, which is the multiway instability M73/M74
# and M111 already document, seen from a new angle.
MULTIWAY_STACK_BUCKET_BB = 5.0


# M128. The postflop counterpart to SIZING_CAVEAT_REASON, and it exists
# for a measured reason rather than a suspected one.
#
# Postflop the derived range is capped to MAX_PATH_QUERY_CLASSES_PER_SIDE
# classes per side — a pure COST control, with no strategic meaning, that
# the user never sees. Sweeping it from 10 to 26 on one flop spot moves a
# value hand's aggression **non-monotonically over a 250x range**:
#
#     cap        10    12    14    16    18    20    22    24    26
#     9s9d set  .003  .004  .019  .025  .771  .071  .122  .393  .402
#     QdQh      .007  .006  .006  .031  .533  .018  .023  .023  .032
#
# Both spike at 18 classes and collapse again; the overpair reverses
# direction five times. This is NOT noise — solving twice at the same cap
# gives a delta of exactly 0.0, so each figure is exact for its cap.
#
# **M127 got this wrong and M128 corrected it.** M127 compared two caps
# (10 and 26), saw 0.25% against 40.2%, and called it a systematic bias
# toward slow-playing. Nine caps show it is not a direction at all. Same
# error shape as M110's "the button opens tighter than under the gun",
# which M111 withdrew: a two-point difference read as a trend.
#
# Widening is not an escape either — cap 26 takes one flop decision from
# 10.8s to 52.1s, 4.8x — so no setting is both affordable and stable.
# Until the cause is understood, the honest move is the one M98 already
# established for preflop sizing: say so.
#
# Scoped to the AGGRESSION axis and to postflop only. Whether to continue
# is not implicated — that is the fold/play call, which held up across
# 275 advised decisions in M127's play session.
# M130 sharpened this. M128 could only say the aggression axis was
# "unstable"; the mechanism is now measured, and it is specific enough to
# tell a user something they can act on.
#
# `_cap_range` keeps the top classes BY HOW OFTEN THEY TOOK THE OBSERVED
# ACTION. Premium hands MIX — at 100bb the raiser's AA raises 0.495 of
# the time because it also jams, KK 0.765, AKo 0.208 — while mediocre
# hands raise purely at 0.99+. The tenth-place cut is 0.9912, so every
# premium falls below it. **In 5 of 6 measured (stack, position) cases
# the raiser's modelled range contained no premium hands at all**, though
# they are genuinely in the real range. (The big blind's exclusion is
# correct and different: premiums 3-bet rather than call, so their
# calling frequency really is 0.)
#
# That is why value hands check: with no big pairs in the opponent's
# model there is nothing to raise for value against.
#
# **M138 correction: that sweep did NOT converge, and the reference it
# established was wrong.** It read .003 / .694 / .468 / .301 / .336 /
# .347 at caps 10/18/26/34/44/60 and called the last three "settling
# near 0.35". They are three flat-looking points inside too narrow a
# window: measured through /advise and carried out further, the same
# spot reads 0.381 / 0.5948 / 0.9186 at caps 60 / 100 / 200, and 0.987
# once the uncapped solve is given 2,500 iterations. The uncapped solve
# is the trustworthy one — doubling equity samples moves it 0.9186 ->
# 0.9206 and the seed does not move it at all — so a flopped set bets
# ~0.99 here, not ~0.35. This is the same error M110/M111 recorded:
# reading a trend off too few points.
# M138. The numbers the caveat quotes, and what they are measured
# against. The reference is cap 200 (uncapped: all 169 classes), 200
# equity samples, 2,500 iterations — ~850s per spot, which is why no
# earlier milestone used it. It is stable where the old cap-60 reference
# was not: doubling samples moves 9s9d 0.9186 -> 0.9206, and the equity
# seed does not move it. It is still drifting slightly toward the
# extremes with iterations, so these errors are a LOWER bound.
#
# M140 widened this from 5 spots to 16, across the strength ladder, and
# the defect is larger and has a named worst case.
#
#   spot                          hand              ref    shipped   error
#   2h6d9c / 7h8h    open-ended straight draw    0.0001    0.8811   +0.8810
#   2h6d9c / 9s9d                     top set    0.9870    0.5489   -0.4381
#   2h6d9c / 8h7d    open-ended straight draw    0.0037    0.4142   +0.4105
#   2h6d9c / 7s8s    open-ended straight draw    0.0020    0.1720   +0.1700
#   2h6d9c / QdQh                    overpair    0.0014    0.1501   +0.1487
#   2h6d9c / 2s2c                  bottom set    0.0385    0.1261   +0.0876
#   2h6d9c / 9h8h            top pair + bdfd     0.0003    0.0283   +0.0280
#   2h6d9c / TsJs      overcards + backdoor      0.0003    0.0202   +0.0199
#   2h6d9c / 6s6c                  middle set    0.5940    0.5784   -0.0156
#   Ac7d2h / 7s7c                  middle set    0.0010    0.0134   +0.0124
#   2h6d9c / AhKh                         air    0.0001    0.0109   +0.0108
#   Ac7d2h / AsKs         top pair top kicker    0.0003    0.0060   +0.0057
#   Ac7d2h / KsQs                         air    0.0003    0.0014   +0.0011
#   2h6d9c / 5s7s                     gutshot    0.0000    0.0006   +0.0006
#   Ac7d2h / 4h5h                     gutshot    0.0000    0.0000    0.0000
#   Ac7d2h / 8s9s        no draw, no pair        0.0000    0.0000    0.0000
#
# **Open-ended straight draws are the named failure, 3 of 3.** The
# product recommends a ~2.3x-pot overbet (`raise:12.50`) 0.88 of the time
# on 7h8h where the converged solve checks 100% — reproducible three
# times byte-identical, reference converged at that spot (0.0004 /
# 0.0001 / 0.0 at 1k / 2.5k / 5k iterations). Gutshots and the no-draw
# control are clean, so this is specific to open-enders, not draws
# generally.
#
# **And it is incoherent WITHIN the class**: 7h8h / 8h7d / 7s8s are the
# same 78 open-ender on a rainbow board with near-identical true
# frequencies (0.0001-0.0037), yet ship 0.8811 / 0.4142 / 0.1720 — 5x
# apart on suits alone, and ranked in the OPPOSITE order to the
# converged solve.
#
# Direction, stated as measured rather than as it would be most useful:
# the error tracks the TRUE frequency (high-frequency hands under-bet,
# low-frequency over-bet) on all 16 spots. The tempting summary — "we
# under-bet your strongest hands" — is false across boards, since middle
# set flips sign between 2h6d9c (0.594 true, under) and Ac7d2h (0.001
# true, over). Only the open-ender category is both consistent and
# nameable by a player.
#
# Each widening of the spot set has made the defect look LARGER (5 spots
# 0.1222/0.4381 -> 16 spots 0.1394/0.8810), so treat these as lower
# bounds.
# M141. Why no cap setting fixes this, measured on the 16-spot set:
# **the cap moves error BETWEEN hand types rather than reducing it.**
# Samples and iterations fixed, cap the only variable:
#
#   group                      cap 26    cap 34    cap 44
#   made hands (sets/pairs)    0.1052    0.2022    0.2458
#   draws                      0.2924    0.0031    0.0784
#   air / overcards            0.0080    0.0018    0.0047
#
# Made-hand error grows monotonically with width; draw error collapses.
# This is the clearest account yet of why nine ideas failed (M130-M138):
# each was a single-knob reweighting of the same 169 classes into a fixed
# budget, so each could only redistribute error between hand types, never
# remove it — which is why nine different rules all landed in the same
# 0.09-0.14 band.
#
# **Cap 34 looks better on the raw 16-spot mean (0.0899 vs 0.1394) and is
# NOT adopted.** Excluding the single worst draw flips it (0.0953 vs
# 0.0899 for cap 26); it wins 8 of 16 and loses 6; it makes the WORST
# case worse (0.7635 vs 0.4381); it costs 2.7x (23.0s vs 8.6s); and the
# spot it damages most is top set, where 0.2235 against a true 0.987
# means under-betting the strongest possible holding by three quarters.
# Getting top set wrong costs more than getting a draw wrong, because
# with top set the money goes in. Draws are warned about instead, in
# POSTFLOP_AGGRESSION_CAVEAT_REASON.
#
# Considered and rejected without trying: choosing the cap from HERO's
# hand type (34 for draws, 26 otherwise). It would be tuning on the
# 16-spot set that produced the split, and it is incoherent on its face —
# the opponent's range does not depend on hero's cards.
# M142 / F38. **The fold-versus-play call is NOT the sound half, and
# this caveat used to tell users it was.** Every measurement behind that
# claim was taken at a street's OPENING decision, where folding is not
# even a legal action because checking is free. Measured at a node
# FACING A BET (flop_action_path=["raise"]), against the same converged
# reference, 10 spots:
#
#   hand                fold err   aggression err   shipped shove
#   top set               0.0000           0.0000          0.9904
#   middle set            0.0000           0.0000          0.8807
#   overpair              0.0000           0.3581          0.9636
#   open-ender            0.0042           0.0057          0.9990
#   top pair              0.0003           0.1454          0.1831
#   gutshot / weak        0.0109           0.0108          0.0001
#   overcards (TsJs)      0.1695           0.2162          0.2370
#   overcards (AhKh)      0.3222           0.0078          0.0051
#   air (8s9s)            0.5608           0.5573          0.5672
#   air (KsQs)            0.8017           0.3926          0.0639
#
#   mean fold error 0.1870 (worst 0.8017)
#   mean aggression error 0.1694 (worst 0.5573)
#
# The two axes are comparably wrong here, so "the fold call is far
# sounder" is withdrawn. Strong hands are fine — top set, middle set and
# the open-ender all shove ~0.99 and the converged solve agrees. The
# failure is concentrated in WEAK hands facing a bet, and it is not
# merely over-calling: **with nine-high (8s9s on Ac7d2h) the product
# recommends shoving 97.5bb 0.5672 of the time where the correct play is
# to fold 0.9869.** Verified byte-identical across runs, with the
# reference identical at 1,000 / 2,500 / 5,000 iterations.
#
# Why nothing caught it: M127's play session judged 275 decisions
# CATEGORICALLY (premiums never folded, trash folded). This solver does
# fold air sometimes and never folds premiums, so it passes every such
# check while folding at a quarter of the correct rate. And M138-M141's
# 16-spot sweeps all measured the opening decision, a different node.
POSTFLOP_FOLD_ERROR_MEAN = 0.1870
POSTFLOP_FOLD_ERROR_WORST = 0.8017

# M167 CORRECTS M166. M166 shipped a threshold of 0.55 on the claim that
# postflop error splits by hand strength - weak hands unreliable, stronger
# hands fine. Pooling every spot measured (44, three studies, all drawn
# from real play) does not support it:
#
#   correlation between strength percentile and error: -0.130
#
#   band        n   mean err   worst   over 0.10
#   0.00-0.20  10     0.0510   0.182       3
#   0.20-0.40  10     0.2686   0.993       4
#   0.40-0.55   7     0.0745   0.270       2
#   0.55-0.75   8     0.1252   0.990       1     <- "reliable" under M166
#   0.75-0.90   7     0.0079   0.023       0
#   0.90-1.01   2     0.0374   0.057       0
#
# Errors occur at every band below 0.65, including one at 0.64 that M166
# would have called reliable. M166's own numbers came from 27 spots and
# were, in part, sampling luck - the pattern broke on the next 18.
#
# What DOES hold, and all that is now claimed: **the top of the range is
# clean.** Nine spots at or above 0.75, none over 0.10, mean 0.014, worst
# 0.057. Below that, 10 of 35 spots are off by more than 0.10 and hand
# strength cannot say which - so the signal certifies reliability where it
# was measured and stays silent otherwise, rather than asserting a
# split that does not exist.
#
# The firing rate falls out of that rather than being chosen: the strong
# note lands on the minority of hands that earned it, instead of the 52%
# of postflop decisions M166's weak warning reached.
# M168: the certification above is FLOP-ONLY, and this is the measurement
# that forced it. M167 applied the threshold on all three streets because
# it was cheaper than checking. Checked, on eight turn spots drawn from
# real play:
#
#   band                     n   mean err   worst   over 0.10
#   not known (<0.75)        4     0.0728   0.138       1
#   reliable   (>=0.75)      4     0.2960   0.588       3
#
# **The relationship inverts.** On the turn the band the product was
# certifying is the WORSE one - three of four spots over 0.10, worst
# 0.588, on a hand at percentile 0.977. A player holding a strong turn
# hand was being told the advice measured reliable while it was off by
# more than half.
#
# The asymmetry that decides this: certifying reliability needs positive
# evidence, withdrawing a certification needs only the absence of it -
# and here the evidence actively contradicts. Four spots per band is a
# thin sample to CONCLUDE from and an ample one to STOP claiming on.
#
# The river is unmeasured and therefore also uncertified: after the turn
# inverted, assuming the river behaves like the flop would be the same
# mistake twice.
# M180 WITHDREW the flop's certification, leaving no street certified.
#
# M167 granted it on **9 spots** at percentile >= 0.75: mean 0.0144,
# worst 0.0571, ZERO over 0.10 — measured at cap 26 with 500 iterations.
# M172 then changed both (cap 100, iterations 250) and it was never
# re-run.
#
# Re-measured on **28 strong-band spots** across four cells
# ({opening, facing a bet} x {strong, weak}), against a full-range
# 169-class reference at s200/i2500 built at each request's own pot and
# stack:
#
#   arm                        mean    worst   over 0.10
#   cap 100 (shipped)        0.1004   0.9535        6/28
#   opening decisions only   0.1480   0.9535        4/14
#
# **It is not coverage**: the strong band fails at cap 100, 140 AND 169
# (uncapped) — 6, 7 and 9 spots over 0.10. **It is not precision**:
# holding the cap and varying iterations 250 / 500 / 2500 gives 6, 6 and
# 5 over 0.10, flat, consistent with M152's finding that precision is a
# dead axis here.
#
# The worst case is what a certificate should never cover: **Kc8c on
# 7h9hKd at percentile 0.913 — top pair on a two-flush board. The
# reference bets 0.9987; the product bets 0.045** and told the user the
# answer measured reliable. Its reference drifted 0.0003 between seeds,
# so that is not a reference artifact.
#
# **No threshold rescues it** — the failures include percentiles 0.913
# and 0.978, so raising the bar does not find a clean band.
#
# This is the FOURTH claim in this project overturned by measuring more
# of the same thing (M166 at 27->45 spots, M168 at 4->12 per band, M110's
# positional read, and now this). The asymmetry that governs the action
# is already recorded: certifying needs positive evidence FOR a street;
# withdrawing needs only its absence — and here there is positive
# evidence AGAINST.
#
# Restoring a certificate needs a fresh study at >= 28 spots per band
# with a stability-checked reference, not a re-run of M167's nine.
CERTIFY_RELIABILITY_ON_STREETS = ()

# M180: recorded so the copy above cannot drift from the measurement.
FLOP_MEASURED_SPOTS = 56
FLOP_MEASURED_FAILURES = 8

# M180. The flop can no longer use UNMEASURED_STREET_NOTE — that note
# says accuracy "has not been measured against a larger solve the way the
# flop has", which is self-contradictory when shown ON the flop. The flop
# HAS been measured, more than any other street; the result is that its
# advice cannot be certified.
#
# **Deliberately claims no direction.** Neither split is separable at 56
# spots: strong vs weak is 1.32 sigma (0.1004 vs 0.0381) and opening vs
# facing-a-bet is 1.83 sigma (0.1118 vs 0.0267). M166 asserted exactly
# this kind of split from a smaller sample and M167 had to withdraw it,
# so the note says the error is NOT predictable rather than inventing a
# rule the measurement does not support.
FLOP_MEASURED_NOTE = (
    "RELIABILITY ON THE FLOP HAS BEEN MEASURED, AND IT IS NOT GOOD ENOUGH TO "
    "CERTIFY. Against a fuller solve over 56 flop spots, about one answer in "
    "seven was off by more than 0.10 in how often it bets or folds — and neither "
    "how strong your hand is nor whether you are facing a bet predicts which "
    "answers those are. The worst case measured was a top pair that a fuller "
    "solve bets almost always, where this advice checks. Treat the action as a "
    "suggestion rather than a solved answer. "
)

FLOP_CERTIFICATION_SPOTS = 28
FLOP_CERTIFICATION_FAILURES = 6
FLOP_CERTIFICATION_WORST_ERROR = 0.9535

# M175 corrected the second sentence. It used to say that on the turn the
# hands this advice would call reliable "were the LEAST accurate ones
# tested" — M168's inversion. Re-measured against the M173 standalone
# solve over 24 spots (12 per band), that is false: the certified band
# means 0.0954 against the weak band's 0.1032, and the strength/error
# correlation is +0.057. Strength does not invert on the turn; it carries
# no signal there at all. Both statements refuse certification, but only
# the true one stops a reader concluding that WEAK turn hands are the
# safe ones — 2 of 12 of those exceed 0.10 error too.
UNMEASURED_STREET_NOTE = (
    "HOW RELIABLE THIS PARTICULAR ANSWER IS, IS NOT KNOWN. Accuracy on this street "
    "has not been measured against a larger solve the way the flop has, and hand "
    "strength does not substitute for it: on the turn, where this was tested "
    "directly, the advice was off by more than 0.30 at BOTH ends of the strength "
    "range, and how strong your hand is did not predict which answers were the "
    "accurate ones. Treat the action as a suggestion and avoid committing a large "
    "part of your stack on it alone. "
)

# M175's measurement, recorded so the note above cannot drift from it.
# 24 turn spots from real play, 12 per strength band, each scored against
# a full-range (169-class) standalone reference solved twice — every
# reference held to drift 0.0, so none was discarded.
#
#   band                    n     mean   median    worst   over .10
#   below 0.75             12   0.1032   0.0141   0.5541          2
#   at or above 0.75       12   0.0954   0.0533   0.3038          4
#
# The flop's certificate rests on 9 spots at or above the threshold with
# a worst of 0.0571 and NONE over 0.10. The turn misses that bar in the
# certified band specifically, which is the band a certificate would
# vouch for.
TURN_CERTIFIED_BAND_WORST_ERROR = 0.3038
TURN_CERTIFIED_BAND_SPOTS_OVER_TENTH = 4
TURN_RELIABILITY_SPOTS_PER_BAND = 12
# Rounded DOWN for the user-facing note: quoting a number the measurement
# does not reach would overstate it.
TURN_RELIABILITY_QUOTED_WORST = 0.30

# M177: the river HAS been measured now, and the note above is wrong for
# it in two ways — so the river gets its own.
#
# 56 river spots from real play, four cells, each scored against a
# full-range (169-class) reference WITH a real size menu, built at the
# request's own OPENING pot and solved twice:
#
#   cell               n   mean    worst   over .10
#   opening / strong  14  0.1812  0.5082      6/14
#   opening / weak    14  0.0425  0.4971      1/14
#   facing  / strong  14  0.3411  0.9993      8/14
#   facing  / weak    14  0.0555  0.2864      2/14
#
# CERTIFICATION IS REFUSED: 14 of 28 strong-band spots exceed 0.10,
# against a rule fixed before the data (zero, the bar the flop cleared).
#
# **The flop's threshold points the WRONG WAY here.** Error concentrates
# in the band a certificate would vouch for — strong hands fail 50% of
# the time, weak hands 11% — and the mechanism is consistent: the river
# OVER-COMMITS with strong-but-not-nutted hands. Kc8s at percentile 0.921
# commits the stack 1.0 where the reference checks 0.995; 8hJs at 0.895
# shoves 0.97 where the reference checks 0.9988.
#
# Stated as measured, not as a law. M168 claimed this shape for the turn
# from 4 spots per band and M175 withdrew it at 12. This is 14 per cell
# across BOTH node types, the gap is 4-6x rather than marginal, and it
# grew rather than shrank from n=8 — but it is still 56 spots.
#
# **Facing a bet is the worse node type and had never been measured on
# any street.** All 368 river spots across ten benchmark sessions are
# opening decisions, so the harness never produces one; these were
# constructed. F38 is what that blind spot cost last time.
RIVER_CERTIFICATION_REFUSED_SPOTS = 28
RIVER_CERTIFICATION_FAILURES = 14
RIVER_STRONG_BAND_MEAN_ERROR = 0.3411
RIVER_WEAK_BAND_MEAN_ERROR = 0.0555
RIVER_SPOTS_PER_CELL = 14

RIVER_MEASURED_NOTE = (
    "RELIABILITY ON THE RIVER HAS BEEN MEASURED, AND IT IS WORSE FOR STRONG HANDS. "
    "Against a fuller solve over 56 river spots, advice for hands in the top quarter "
    "by strength was wrong more than three times as often as advice for weak ones "
    "(14 of 28 against 3 of 28), and it errs in one direction: it recommends putting "
    "chips in more often than the fuller solve does. If you hold a strong but not "
    "unbeatable hand here, especially facing a bet, treat a recommendation to commit "
    "your stack with particular suspicion. Weak-hand advice on this street measured "
    "accurate. "
)

# M185: where the measured cost of this advice actually is.
#
# M183 priced 48 real decisions in chips instead of frequency distance.
# The cost is concentrated by NODE TYPE, and separably so:
#
#   facing a bet   n=24  mean |loss| 0.3107 bb   median 0.0235
#   opening        n=24  mean |loss| 0.0569 bb   median 0.0126
#   delta +0.2538 +/- 0.0983 (2.58 sigma); permutation p = 0.0054
#
# 5.5x, and it holds on every street (flop 19.5x, turn 6.7x, river 1.8x).
# Facing-a-bet nodes are half the decisions and carry **85% of all loss**.
#
# **This is a COARSE signal and is described as one.** A finer runtime
# detector was looked for and does not exist: the best runtime-visible
# predictor of |loss| is the action count at +0.237 (itself a proxy for
# this same split), against +0.562 for TVD and +0.772 for TVD x spread —
# both of which need a reference solve and cannot be computed live.
# Value spread, entropy, all-in mass, max probability and hand strength
# were all measured and are all weak; spread in particular adds NOTHING
# over this flag, since facing-a-bet nodes simply ARE the high-spread
# ones (corr within facing nodes: -0.109).
#
# The note is careful not to overstate: the MEDIAN facing-a-bet decision
# costs 0.0235 bb, which is nearly as cheap as an opening one. It is the
# tail that differs, so the wording says the cost concentrates here rather
# than that this answer is probably wrong.
# M188 re-measured at **801 facing-a-bet spots** (267 per cell), sampling
# only the cells that carry the cost — M187 showed the three opening
# cells were settled and nearly free.
#
# TWO CORRECTIONS to what M187 shipped in this note:
#
# 1. **86%, not 96%.** The 96% was the share of raw |loss| within a
#    BALANCED 50/50 spot set, which is not what a player meets. Weighted
#    by how often each decision type actually occurs, facing-a-bet
#    carries **86%** of the cost. The unweighted figure flattered it.
# 2. The ratio is **27.4x** (5.64 -> 7.17 sigma), still rising but
#    decelerating: +9.3, +7.1, +5.5 across the four sample sizes. Quoted
#    as "at least 25 times" — still deliberately a floor.
#
# The tail is NOT exhausted. The worst single decision found went
# 11.45 bb at 201 facing spots to **73.25 bb** at 801 — a river call/fold
# in a 30bb pot, verified real (its action spread is 76.81, so the loss
# is inside what the decision can physically swing, and all 801 spots
# pass that check). One spot in 267 moves its cell's mean by 0.27 bb.
FACING_A_BET_COST_NOTE = (
    "THIS IS THE KIND OF DECISION THIS ADVICE GETS WRONG MOST EXPENSIVELY. Priced "
    "against a fuller solve over 801 real spots facing a bet, these decisions carry "
    "about 86% of everything the advice costs, averaging at least 25 times more than "
    "decisions where you act first. Most individual answers here are still accurate "
    "— the median one costs almost nothing — but 18% of them cost more than a big "
    "blind and 5% cost more than five, because folding being available is what makes "
    "it possible to lose a lot. Weigh this one more carefully than its confidence "
    "label alone suggests. "
)

# M189: the sharpest runtime signal available, and it is much sharper
# than M185's.
#
# M185 flagged every facing-a-bet decision — 32.6% of postflop play,
# catching 93% of the cost, lift 2.9x. A warning on a third of all
# decisions is close to a warning on everything.
#
# Splitting those 801 spots by hand strength shows the cost is
# concentrated in a BAND, and non-monotonically — both the weakest and
# the strongest hands are cheap:
#
#   strength    % costing >1bb   mean |loss|
#   0.00-0.20             4.1%        0.129
#   0.20-0.40             4.9%        0.200
#   0.40-0.55            12.3%        0.443
#   0.55-0.75            29.0%        1.376
#   0.75-0.90            43.8%        2.531
#   0.90-1.01            11.5%        0.541
#
# That is the "is my top pair actually good?" decision: strong enough to
# continue, not strong enough to be obvious. Facing a bet AND landing in
# 0.55-0.90 **fires on 12.1% of postflop decisions and catches 74% of all
# cost — lift 6.1x**, against M185's 2.9x.
#
# **Replicated before shipping**, because a band found by inspecting the
# same data that suggested it is exactly how M166 went wrong. Split-half
# on a random partition: in-band mean |loss| **1.874 in BOTH halves**,
# out-of-band 0.267 and 0.307 — ratios 7.0x and 6.1x.
#
# The coarser note still fires outside the band, because out-of-band
# facing decisions average 0.28-0.31 bb against an opening decision's
# 0.032 — cheaper, not cheap.
COSTLY_BAND_LOW = 0.55
COSTLY_BAND_HIGH = 0.90

COSTLY_BAND_NOTE = (
    "AND YOUR HAND IS IN THE RANGE THIS ADVICE HANDLES WORST. Hands roughly between "
    "the 55th and 90th percentile — good enough to continue, not good enough to be "
    "obvious — are where the measured cost concentrates: facing a bet holding one, "
    "44% of decisions were off by more than a big blind, against 4% for weak hands "
    "and 12% for very strong ones. This combination is 12% of postflop decisions and "
    "carries 74% of everything the advice costs. If you are going to override this "
    "engine anywhere, here is where your own judgement is most likely to beat it. "
)

RELIABLE_HAND_STRENGTH_PERCENTILE = 0.75

RELIABLE_HAND_ERROR_MEAN = 0.0144
RELIABLE_HAND_ERROR_WORST = 0.0571
UNCERTAIN_SHARE_OVER_TEN_POINTS = 0.29

RELIABLE_HAND_NOTE = (
    "Your hand is in the range where this advice measured reliable: across every "
    "spot tested at this strength the betting frequency was off by at most 6 "
    "percentage points, and none by more than 10. "
)

UNCERTAIN_HAND_NOTE = (
    "HOW RELIABLE THIS PARTICULAR ANSWER IS, IS NOT KNOWN. Across spots at this "
    "hand strength, about one in four had its betting frequency off by more than 10 "
    "percentage points, and the worst by 99 - in both directions, so the error "
    "cannot be corrected for. Hand strength does not identify which answers are "
    "affected. Treat the action as a suggestion and avoid committing a large part "
    "of your stack on it alone. "
)

POSTFLOP_AGGRESSION_ERROR_MEAN = 0.1394
POSTFLOP_AGGRESSION_ERROR_WORST = 0.8810

# M144/F40. Appended when the node offered no intermediate bet size at
# all — the river, at production settings.
# M151. What the missing river sizes actually COST, measured rather than
# assumed. F40 (M144) framed the gap as "cannot tell you how much to
# bet"; re-solving the same river spot with one normal size (0.75x pot)
# available shows it changes the ACTION, in both directions:
#
#   hero            shipped check   with a size        shipped all-in
#   AsKs top pair          0.9941   0.6449 (bets .35)          0.0059
#   KhQd top pair          0.9941   0.5206 (bets .48)          0.0059
#   9s8s nine-high         0.0121   0.0048 (bets .98)          ~0.988
#   7d7h middle pair       0.7055   0.9511                     0.2945
#   AcQc ace-high          1.0000   0.9982                     0.0000
#
# With all-in the only way to bet, the strategy collapses into
# check-or-shove: value hands check when they should bet small, and
# bluffs move a whole stack into a 5bb pot when they should bet 3.75.
#
# **Why this is disclosed and not fixed.** `solve_flop_to_river` takes ONE
# `raise_sizes` for all three streets, so enabling river sizes widens the
# flop and turn too — and that chain's DEFAULT 20 iterations already
# costs 63-105s. A standalone river solve is cheap (~7s, and river equity
# is exact rather than sampled because the board is complete), but it
# would use ranges that skip flop/turn narrowing. Checking is itself an
# action with frequencies, so even a checked-through line carries
# information: the approximation is real in every line, and trading a
# known gap for an unvalidated model is not an improvement.
BET_SIZING_COVERAGE_NOTE = (
    " Note also that on this street the solver modelled only checking or calling and "
    "going all-in: no intermediate bet size was a legal action in its tree. So a low "
    "all-in frequency here does not mean a smaller bet was considered and rejected — "
    "smaller bets were never available. That distorts the PLAY here, not just the size. "
    "Measured against the same spot re-solved with one normal bet size available: a top "
    "pair that should bet about a third of the time instead checks 99%, and a busted "
    "draw that should bet a third of the pot instead moves all in 99% of the time. "
    "Treat the checking and the all-in frequencies on this street as unreliable in both "
    "directions, and do not read a high check frequency as a reason to give up on a "
    "value hand."
)

# M145/F41. `solver_confidence` was a pure function of TABLE SIZE and
# knew nothing about whether the node it is describing was ever trained.
#
# Measured: a 3-max river (Kd7c2h Ts 4c) returns **0 of 136 hands
# trained, every row exactly the uniform prior** - hero reads
# `call_or_check 0.3333 / raise:18.75 0.3333 / all_in:97.50 0.3333` -
# while the response says `solver_confidence: "high"` and
# `range_confidence: fully_trained: true` for all three positions. Two of
# three confidence signals vouch for an answer that was never computed.
#
# It is OCCASIONAL, not systematic: 1 of 6 measured (3-max/6-max, four
# boards) came back fully untrained, the rest 46-50 of ~130. That makes
# it worse for a user than a consistent gap - most requests look fine and
# nothing distinguishes the one that is not.
#
# `range_confidence` is not wrong here, which is why it misleads: it
# reports the PREFLOP range derivation, and those classes really were
# fully trained. Composed with a river strategy that was never solved, it
# reads as an endorsement of the answer.
# M149/F43. **`trained` means VISITED, not LEARNED — and the gap is
# user-visible on the most expensive decision in preflop poker.**
#
# Measured: a 6-max player holding AA facing a 4-bet is told **fold
# 0.3333 / call 0.3333 / all-in 0.3333** — an exactly uniform split —
# while the response reports `hero.trained: true`, `solver_confidence:
# "high"`, and 101 of 169 hands trained at the node. Folding aces to a
# 4-bet a third of the time is a stack-losing instruction.
#
# Why nothing caught it. F41/M145 flags a node where NOTHING is trained;
# here most of the node IS trained, so that signal correctly stays quiet.
# `trained_mask()` asks whether a hand accumulated any strategy_sum, i.e.
# whether it was VISITED. `current_strategy()` returns the uniform prior
# whenever every regret is <= 0 — M73 measured ~70% of rows all-negative
# — so a hand can be visited repeatedly and still average to exactly the
# prior. The distinction was documented in the solver and never connected
# to the honesty signals.
#
# Heads-up is unaffected: measured at the same spots the exact solver
# returns real answers (BTN opens 0.998, facing a 4-bet jams 1.0). This
# is the sampled multiway solver failing to reach deep preflop nodes.
#
# Scoped to EXACT uniformity. A near-uniform row (0.3334 / 0.3333 /
# 0.3333) is a real computed answer that happens to be close to
# indifferent, and flagging it would make "low" the normal case. Exact
# equality across every action is the prior's own signature.
# M150. Iterations for solving ONE deep preflop node on demand.
#
# The 6-max preflop tree has **289,036 decision nodes** and the shipped
# solve learns roughly the first four levels — measured on the production
# cached solve, learned rows by depth: 100/100/100% at 0-2, then 80% (d3),
# 48% (d4), 21% (d5), 12% (d6), 3% (d7), **0% at d8+**, where ~285,000 of
# the nodes live. Neither obvious fix applies: 285,000 nodes cannot be
# targeted-trained, and M72/M73 measured 6-max destabilising at 12k
# iterations, orders of magnitude short of covering them.
#
# So this borrows the pattern the postflop path already runs in
# production: `ensure_mccfr_chance_branch` (M75, fixed M146) builds and
# trains a branch when a client actually asks for it, paying only for
# lines someone requests. A deep preflop subtree is SMALL for the same
# reason it is deep — the F43 node (BTN facing a 4-bet, depth 6) has
# **10 nodes** below it.
#
# Measured on that node: AA goes from an even 0.3333 split (101/169
# trained) to **jam 0.9999 at 200 iterations in 2.2s** (169/169 trained),
# and to 1.0 at 1,000 iterations in 8.1s. 200 buys the correct answer;
# the rest buys decimals on a request that already costs seconds.
# M163: the postflop sibling of PREFLOP_DEEP_NODE_TRAIN_ITERATIONS, for a
# MULTIWAY FLOP node that comes back as the uniform prior. M150 solved
# deep preflop nodes on demand; the same defect exists one street later
# and was measured in a 120-hand session, where a six-handed flop
# decision returned 0.3333 across every action.
#
# Affordable only because M162 made multiway equity ~28x cheaper: a whole
# 200-iteration multiway flop solve now costs ~0.19s, so training one
# SUBTREE at twice that budget is a fraction of a second. Before M162 the
# same work would have cost ~11s and would not have been worth doing.
#
# Higher than the preflop constant because the node being repaired is a
# postflop node with concrete combos rather than 169 classes, and F46
# measured multiway flop strategies as noise-dominated at low budgets -
# this does not fix that (see F46), it only replaces "never computed"
# with "computed against a stated prior".
# M165: the HEADS-UP river sibling of the two constants below. A river
# node reached through the flop->turn->river chain gets very little of
# that chain's own 20 iterations, and rows come back as the bare prior
# even though the exact solver visited them - `trained` is true, every
# regret is still <= 0, so `average_strategy` never leaves the uniform
# start. Measured on a real request: 10 of 19 hands at one river node.
#
# 200 rather than 50: the subtree converges by ~50 (0.9992 at 50, 1.0 at
# 200 on the measured spot) and it is a two-action tree over ~19 combos,
# so the extra iterations cost almost nothing and leave margin for spots
# that settle more slowly.
RIVER_NODE_TRAIN_ITERATIONS = 200

MULTIWAY_FLOP_NODE_TRAIN_ITERATIONS = 400

PREFLOP_DEEP_NODE_TRAIN_ITERATIONS = 200

UNIFORM_ROW_REASON = (
    "Your hand's numbers here are an even split across every action, which is the "
    "solver's starting assumption rather than anything it worked out — the hand was "
    "reached during solving but never learned a preference, so this is not a "
    "recommendation to mix evenly. Treat it as no answer for this hand. A different "
    "line, a shallower spot, or heads-up will usually return a real one."
)

# M163/F47: the same news for a hand that was never REACHED. M149 scoped
# `_hero_row_is_the_prior` to `trained is True`, reasoning that a false
# `trained` already fires a louder hero-specific warning. It does - but
# that warning is the `hero.trained` FIELD, and `solver_confidence` never
# saw it, so the headline signal still read "high" over an untrained
# uniform row. Measured in a 120-hand session: one six-handed flop
# decision returned fold/call/all-in at 0.3333 each while calling itself
# high confidence.
#
# Kept separate from UNIFORM_ROW_REASON because the cause differs and a
# user can act on the difference: that one was reached and never formed a
# preference, this one was never reached at all.
UNTRAINED_HERO_ROW_REASON = (
    "Your hand's numbers here are an even split across every action, which is the "
    "solver's starting assumption rather than anything it worked out - this hand was "
    "never reached while solving this spot, so nothing was computed for it. Treat it "
    "as no answer for this hand rather than a recommendation to mix evenly. A "
    "different line, a shallower spot, or heads-up will usually return a real one."
)

UNTRAINED_NODE_REASON = (
    "This spot was not actually solved. Every hand at this decision carries the "
    "solver's starting assumption - an even split across the available actions - "
    "rather than a computed strategy, which is why the frequencies look evenly "
    "balanced. An even split here means \"no answer\", not \"genuinely indifferent\". "
    "Multiway turn and river branches are solved on demand and this one did not get "
    "trained; asking about a different river card, or a heads-up spot, will usually "
    "return a real solve."
)

POSTFLOP_AGGRESSION_CAVEAT_REASON = (
    "How often to bet or raise here is approximate, and so is whether to continue. "
    "To stay affordable this solve "
    "models only part of the opponent's range, chosen by how consistently each hand "
    "took the action they took — and premium hands still get dropped by that rule, "
    "because they mix between raising and going all-in rather than always raising. "
    "Measured against a genuinely uncapped solve across sixteen spots, the raising "
    "frequency is off by about 14 percentage points on average and by 88 at worst, "
    "without a "
    "consistent direction overall. Two cases are specific enough to act on. First: "
    "with an "
    "open-ended straight draw this advice overstates betting badly and in every "
    "case measured — in the worst, it recommends a bet of two and a half times the "
    "pot 88% of the time where the correct play is to check. Discount any "
    "suggestion to bet an open-ended straight draw. Second: facing a bet with a "
    "weak hand, this continues far more often than it should, and sometimes "
    "recommends going all-in — holding nine-high it went all-in 57% of the time "
    "where the correct play is to fold 99%. Fold weak hands facing a bet more "
    "often than this suggests, and treat any recommendation to commit chips with "
    "one as unreliable. With a made hand or a strong draw, the continue-or-fold "
    "call measured essentially exact. Everywhere else the residual has no reliable "
    "direction, so treat the frequency as approximate rather than correcting it "
    "yourself."
)



# M131. Equity samples for the flop path-query solve — 30, against
# board_equity's default of 200.
#
# **The budget was being spent in the wrong place.** A postflop solve
# costs roughly (combo pairs x equity samples), and the shipped setting
# put 200 samples of precision behind a 10-class slice of the opponent's
# range. M130 measured what that slice costs: `_cap_range` ranks classes
# by how PURELY they took the observed action, premiums mix, and so in 5
# of 6 measured cases the raiser's modelled range contained no premium
# hands at all. Precision was being bought for a range whose composition
# was wrong.
#
# Trading precision for width, at roughly fixed cost, is a large win.
# Measured across five (board, hand) spots, each against its own
# widest-affordable reference (cap 60, samples 200, ~175s per spot):
#
#     cap  samples  iters   mean err   max err   wall
#      10      200   1000     0.0944    0.3448    8.4s   <- was shipped
#      18       90    700     0.1366    0.3849   11.7s
#      22       45    600     0.0527    0.1919   12.0s
#      26       30    500     0.0319    0.1387   14.7s   <- shipped now
#      26       30   1000     0.0199    0.0948   21.1s
#
# Mean error falls 3x and the worst case 2.5x. The headline case: a
# flopped set on 2h6d9c was advised to raise 0.3% of the time where the
# reference puts it near 35%; at this setting it comes back at 34.5%.
#
# **Two warnings for anyone tuning these.**
#
# Width is NOT monotonically better. cap 18 measures WORSE than the old
# cap 10 on both mean and max error, which is why the sweep covered
# several points rather than assuming a direction — the same trap M110
# and M127 both fell into.
#
# And accuracy is not free here: every arm that beat the old setting also
# cost more time. This point was chosen deliberately as the knee, giving
# up the last 0.012 of mean error to avoid handing back all of M129's
# speed work.
# M176: build the heads-up board equity table by ranking each combo ONCE
# per runout, rather than redrawing runouts for every pair.
#
# The flop anatomy is why. Measured through /advise at cap 100, a cold
# flop request is 8.62s of which the equity table is **7.71s (89.5%)**
# and the CFR solve is **0.41s (4.8%)** - the exact inverse of M155's
# 14/86, because M161 made CFR O(N) and M172 tripled the combo count the
# O(N^2) table build scales with. CLAUDE.md said "anything aimed at flop
# latency must attack the CFR solve"; that is now 4.8% of the cost.
#
# This is M162's trick applied to the heads-up path, and it wins on every
# axis measured (interleaved in one process, M70):
#
#   cap  26 ( 164 combos)   1.28s ->  0.23s    5.6x
#   cap  60 ( 417 combos)   8.49s ->  0.78s   10.9x
#   cap 100 ( 708 combos)  25.44s ->  1.68s   15.1x
#
# ACCURACY, against a 4,000-sample truth on Th5s7c - it is not merely
# faster, because 320 shared samples net more usable runouts than 30
# per-pair ones:
#
#   per-pair s30 vs truth   mean 0.0496  worst 0.3373
#   shared   s320 vs truth  mean 0.0166  worst 0.1067
#   per-pair vs ITSELF      mean 0.0679  worst 0.4333   <- shipped noise
#   shared   vs ITSELF      mean 0.0214  worst 0.1208
#
# The per-pair builder disagrees with itself under a different seed by
# MORE than shared disagrees with the truth, so this change sits inside
# the noise it replaces while shrinking it 3x.
#
# Correctness is checked where it can be EXACT: on turn and river boards
# both builders enumerate, so dropping collisions leaves precisely the
# deck the per-pair form walks, and they agree to the digit (0.0 over
# 8,460 cells across two boards).
SHARED_RUNOUT_FLOP_TABLE = True

# **INERT ON THE PRODUCTION FLOP PATH SINCE M176 — read this before
# tuning it.** (F48, found M193.)
#
# `parallel_board_equity_table` is what production injects as
# `equity_table_fn`, and with `SHARED_RUNOUT_FLOP_TABLE` on it calls
# `build_shared_runout_equity_table(samples=SHARED_RUNOUT_FLOP_SAMPLES)`
# — deliberately NOT forwarding the caller's `samples`, because 30 is a
# PER-PAIR count and shared runouts need far more of them. That choice is
# correct and documented in `api/parallel.py`; the consequence is that
# this constant no longer changes anything there.
#
# Verified directly rather than reasoned about: `samples=30` and
# `samples=200` on the same board and seed produce **byte-identical**
# tables through the production path (max difference 0.0).
#
# Where it still does something: a direct `solve_flop` call with no
# `equity_table_fn` (tests, standalone studies). On TURN and RIVER boards
# it is ignored regardless, because those enumerate rather than sample
# (M154).
#
# **Two claims this invalidates.** M131 described the postflop budget as
# split three ways — classes, samples, iterations — that "move together";
# samples has not moved anything on this path since M176. And any study
# quoting a reference as "200 samples" was really running 320 shared
# runouts, the same table the shipped solve gets — which is part of why
# the measured gap between them is now so small (M192).
#
# To change the flop's equity precision, change SHARED_RUNOUT_FLOP_SAMPLES.
PATH_QUERY_EQUITY_SAMPLES = 30
