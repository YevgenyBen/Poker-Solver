# Full-table live advisor: deep diagnostic (2026-08-20)

A bug and performance audit of the whole engine, read through one lens:
**what would it take for this tool to give a player real-time decision
advice at every point during a live, full-table (3-9 handed) hand** —
not just heads-up, not just preflop. Two parallel deep-reads of the
core engine (`cfr.py`/`equity.py`/`hand_eval.py`, and
`game_tree.py`/`chance.py`/`solver.py`/`library.py`), plus direct
empirical benchmarking and probing of both the shipped endpoints and
the missing capability, done together. Nothing here was fixed —
per instructions, this is a diagnostic to inform prioritization, not a
milestone. No code changed as a result of this document.

**Headline, upfront:** the most important finding is not about the
*missing* multiway-postflop feature — it's that **the already-shipped,
live `GET /solve?players=9` endpoint has a confirmed, causally-
demonstrated correctness bug**, not just noise, and it has zero test
coverage on the exact code path responsible. See §4.1. Everything else
is organized after that.

## Table of contents

1. [Scope and method](#1-scope-and-method)
2. [What "full table, every decision point" requires vs. what exists](#2-what-full-table-every-decision-point-requires-vs-what-exists)
3. [Critical correctness bugs, ranked](#3-critical-correctness-bugs-ranked)
4. [The structural gap: postflop solving at 3+ live players](#4-the-structural-gap-postflop-solving-at-3-live-players)
5. [Performance benchmarks](#5-performance-benchmarks)
6. [Prioritized recommendations](#6-prioritized-recommendations)

---

## 1. Scope and method

Two focused audits ran in parallel, each reading a distinct part of the
engine end to end with instructions to verify claims by tracing real
code (not trusting existing CLAUDE.md prose) and to state a confidence
level per finding:

- **Audit A** — `poker_solver/cfr.py`, `poker_solver/equity.py`,
  `poker_solver/hand_eval.py`: correctness of the N-player (3+) solving
  path itself.
- **Audit B** — `poker_solver/game_tree.py`, `poker_solver/chance.py`,
  `poker_solver/solver.py`, `poker_solver/library.py`: whether the
  multi-street (flop→turn→river) chaining machinery could support 3+
  live players, and precisely what's missing if not.

Both audits ran real, targeted experiments against the shipped code —
not just reading — including an A/B comparison that changes exactly one
constant and re-runs a real 9-max solve, and direct instrumentation of
a real 300-iteration 9-max solve to see which code paths actually fire
and how often. I independently spot-checked the two most consequential
findings by reading the cited lines directly (§3.1, §3.4) before
writing them into this document, and ran my own additional experiments:
a fresh benchmark sweep of every live endpoint at production scale, and
a standalone probe measuring what a board-aware N-way equity primitive
— the piece that doesn't exist anywhere in the codebase today — would
actually cost, reusing the project's own vectorized hand evaluator.

## 2. What "full table, every decision point" requires vs. what exists

The v3 vision (CLAUDE.md) is a live-table advisor: a user mid-hand
describes what happened — any action sequence, any street, eventually
multiway — and gets advice for that exact situation. Breaking that into
its two axes:

|                    | Heads-up (2 players)                | Full table (3-9 players)                  |
|--------------------|--------------------------------------|--------------------------------------------|
| **Preflop**        | ✅ Any real action path (M16-M25)    | 🟡 Curated hand pool only, not any hand; **and §3.1's bug affects this today at N≥3** |
| **Flop**           | ✅ Any real preflop→flop path (M24)  | ❌ Does not exist; see §4 for exactly why  |
| **Turn**           | ✅ Any real path to a dealt card (M26) | ❌ Does not exist |
| **River**          | 🟡 Engine-proven cheap, not exposed live | ❌ Does not exist |

So today: heads-up is closed end-to-end through the turn (river is one
measured-cheap milestone away). Full-table is closed **only** at
preflop, **only** for a small curated hand pool, and — this is the
headline finding — **the preflop full-table solve that does exist has
a real, demonstrated correctness bug that gets worse exactly as the
table gets fuller** (§3.1). Multiway postflop, at any street, does not
exist in any form and is not a small gap (§4).

## 3. Critical correctness bugs, ranked

### 3.1 [SEVERE — live in production today] The N-way equity placeholder is only neutral at N=2

**Where:** `poker_solver/equity.py:341` and `:363`.

When `MultiwayEquityCache` can't physically deal a sampled opponent
combination (or a candidate hand alongside it), it falls back to a
hardcoded equity of `0.5`, reasoned as "the true probability of this
exact combination is 0 regardless of what equity we'd assign it, so
any neutral placeholder is fine." That reasoning holds at N=2. It does
not hold once N≥3, for two independent reasons, both confirmed:

1. **The sampler doesn't actually assign these combinations probability
   0.** `cfr.py:510-514` draws each opponent's hand independently from
   the full weighted class distribution, with no card-removal tracking
   between opponents (§3.4) — so physically-impossible combinations are
   generated and *consumed at full weight*, not skipped.
2. **`0.5` is not neutral in an N-way pot.** The neutral share of an
   N-way pot is `1/n_live` (0.25 four-handed, 0.111 nine-handed), not
   0.5. `_mccfr_terminal_value` (`cfr.py:377`) computes `equity_vector *
   pot - invested` — so at a 9-way, ~900bb all-in pot, a `0.5`
   placeholder injects roughly **+350bb of phantom value** where the
   truth for a weak hand is closer to **-45bb**.

**Measured causally, not asserted:** the same 300-iteration, seed-1,
9-max solve (`DEMO_MULTIWAY_HANDS`, the exact pool `GET
/solve?players=9` uses today), with *only* the placeholder changed from
`0.5` to `1/n_live`:

| Hand | Shipped: fold | Shipped: all-in | Fixed: fold | Fixed: all-in |
|------|--------------:|-----------------:|------------:|----------------:|
| AA   | 0.007         | 0.913             | 0.007       | 0.604            |
| AKs  | 0.020         | 0.846             | 0.014       | 0.214            |
| T9o  | 0.204         | 0.534             | 0.410       | 0.237            |
| **72o** | **0.224**  | **0.505**         | **0.867**   | **0.054**        |
| **32o** | **0.216**  | **0.566**         | **0.851**   | **0.038**        |

**The shipped solver currently says UTG at a 9-handed table should jam
100bb with 72o about half the time and fold it only 22%.** This is the
literal output of `GET /solve/100?players=9`, which the frontend's
9-max demo tab serves today. One two-line constant change flips 72o to
fold 87% of the time instead. This isn't sampling noise — it's a
systematic, direction-consistent bias (confirmed further by the
mechanism: CFR+'s regret flooring never discounts accumulated positive
regret, so the inflated values ratchet rather than average out — see
§3.9).

Root cause chain: §3.4 (card-removal-ignorant sampling) creates the
impossible combinations → this section's placeholder assigns them wildly
wrong value → CFR+'s regret flooring (§3.9) makes the resulting bias
sticky rather than self-correcting with more iterations.

### 3.2 [SEVERE — live in production today] `deal_n_hands`'s failure path is an exponential-time stall, and it's the real reason 9-max's iteration budget is small

**Where:** `poker_solver/equity.py:189-208`.

When a set of opponent hands is infeasible, `deal_n_hands` proves it by
exhausting the full backtracking search space (up to ~12^N suit
assignments) before raising. Measured wall-clock for one infeasible
call, scaling with hand count:

| Hands | Time to raise |
|-------|---------------:|
| 3     | 0.0006s        |
| 5     | 0.036s         |
| 6     | 0.15s          |
| 7     | 1.80s          |
| **8 (9-max)** | **21.6s** |

And the rate at which this actually fires, measured directly inside a
real 9-max solve — infeasible opponent-tuple rate by live-opponent
count:

| Live opponents | `DEMO_MULTIWAY_HANDS` (shipped pool) | Full 169-class pool |
|-----------------|---------------------------------------:|----------------------:|
| 2               | 0.00%                                   | 0.00%                  |
| 4               | 4.75%                                   | 0.11%                  |
| 6               | 21.8%                                   | 1.47%                  |
| 7               | 35.4%                                   | 3.41%                  |
| **8**           | **50.8%**                               | **6.63%**              |

Inside one real 300-iteration 9-max solve, the worst single stall
measured was **18.0s**, with several more at 11.7s, 8.5s, 8.1s, 6.0s —
a large fraction of the ~93s total solve time is one thread blocking on
proving a single deal impossible.

**This corrects a standing claim in CLAUDE.md and `api/main.py`'s own
module docstring**, both of which attribute 9-max's small (300)
iteration budget to `MultiwayEquityCache`'s cache-hit-rate collapsing
at high opponent counts. That framing is real but incomplete — the
*actual* per-iteration cost driver, measured directly, is this
backtracking stall, not the cache. This matters for prioritization: the
stated cause (combinatorial cache-hit collapse) has no cheap fix; this
one does — an O(N) rank-count feasibility precheck before attempting
the full backtrack would eliminate the multi-second stalls outright,
and combined with §3.1's fix, would remove much of the reason 9-max's
budget was ever capped so low.

### 3.3 [SEVERE] The exact code path responsible for §3.1/§3.2 has zero test coverage, and 88% of a real 9-max solve's decision nodes never get trained

Three independent, compounding coverage gaps, all confirmed:

- **Every test that exercises `MultiwayEquityCache` passes exactly 2
  opponents** (`tests/test_equity.py:260-325`). Two-opponent tuples can
  *never* be card-infeasible (two copies of the same class use at most
  4 cards). The whole-vector fallback branch that fires on 43-46% of
  real 7-8-way showdowns (§3.2's table) is **structurally unreachable
  by the entire test suite.**
- The one test that does exercise the per-candidate fallback
  (`test_multiway_cache_handles_blocked_traverser_hand_gracefully`)
  only asserts "no NaN, value in [0,1]" — a placeholder of `0.5` and a
  placeholder of `1/n_live` both pass that assertion, and so would
  0.99.
- The 9-max solver tests' own assertions (`AA fold < 0.2`, `32o fold >
  AA fold`) **both pass on the demonstrably-wrong output in §3.1's
  table** (32o fold 0.216 > AA fold 0.007 — true, and still wrong).

Separately, and more fundamentally: instrumenting a real 300-iteration,
9-max solve shows **1,950 of 2,215 touched decision nodes (88%) have a
`strategy_sum` of exactly zero** — meaning `strategy_at()` returns the
untrained uniform prior for them, indistinguishable in the API response
from a genuinely-converged 25/25/25/25 mix. At the API layer, `GET
/solve/{stack}?players=9&position=BB` returns a flat strategy for 6 of
9 positions today, with `iterations: 300` in the same payload and
nothing distinguishing "solved" from "never sampled." The one existing
test asserting BB's output (`test_nine_max_strategy_for_position_bb_is_
well_formed`) only checks that frequencies sum to 1 — **it would pass
unchanged if `mccfr_solve` returned an empty dict.**

For a tool whose entire purpose is advice at every decision point, this
is the central structural risk: there is currently no way — in the
code or in the API response — to tell a genuinely-converged answer from
one nobody ever computed.

### 3.4 [Root cause feeding 3.1] Opponent-hand sampling ignores card removal between opponents

**Where:** `poker_solver/cfr.py:510-514`. Each opponent's hand is
sampled independently from the full weighted class distribution with
no tracking of cards already assigned to earlier-sampled opponents in
the same iteration — confirmed directly by reading (`rng.choices(hands,
weights=combo_weights)[0]`, called once per opponent position with no
shared "used cards" state). This is the generator of the infeasible
combinations that §3.1 and §3.2 both have to cope with after the fact.
A card-aware sampler (deal all opponents' hands jointly, respecting
removal) would eliminate the infeasibility class at its source rather
than patching around it downstream — but per Audit B, this is also one
of the four real prerequisites for chance-node-aware MCCFR (§4), not a
narrow fix.

### 3.5 [MODERATE] Every suited hand in a multiway showdown is dealt the same two suits

**Where:** `poker_solver/equity.py:36-44` combined with the
first-fit (never randomized) backtracking order in `deal_n_hands`.
Confirmed by direct output: dealing 5 suited hands simultaneously
produces `[('Ac','Kc'), ('Qc','Jc'), ('Tc','9c'), ('8c','7c'),
('6c','5c')]` — every suited class becomes clubs; every offsuit class
becomes clubs+diamonds. At N=2 (the originally-validated scale) this
barely matters. At 7-9 handed, every suited opponent is effectively
drawing to the same flush suit, which systematically distorts flush
frequency, chop rates, and every multiway equity computed from these
deals. The mechanism is confirmed; the magnitude of the resulting
equity error has not been separately measured.

### 3.6 [MINOR] `traverser_equity_vector`'s documented determinism claim is false across opponent-tuple permutations

**Where:** `poker_solver/equity.py:324` (cache key) vs. `:332` (deal
order). The cache key canonicalizes opponent order (`sorted(...,
key=str)`), but the actual cards dealt depend on the *caller's*
unsorted order, and §3.5's first-fit dealing means different orders
produce different concrete cards. Confirmed: `(AKs, T9o, KK)` vs. `(KK,
T9o, AKs)` on fresh caches, same seed, produced equity vectors differing
by up to 0.0069. The docstring's claim ("doesn't depend on which order
... only on the combination itself") is true for *which* combination is
requested, false for which permutation of it arrives first. Low impact
today (each solve builds its own fresh cache), but a latent
reproducibility break the moment a cache is ever shared or persisted
across requests.

### 3.7 [MODERATE, postflop-specific — not yet reachable live, but a landmine] `chance.py` would silently carry a folded player into the next street at N≥3

**Where:** `poker_solver/chance.py:167-175`. `build_chance_node`
threads the *full* `positions` tuple into the next street's
`StreetConfig`, not the subset of positions still live at that
terminal. At N=2 this is invisible — `is_showdown` already requires
both positions live, so `positions` and "the live set" are identical by
construction. At N≥3 they are not: a perfectly ordinary line (OOP bets,
MID raises, IP calls, OOP folds) is showdow-eligible with 2 live and 1
folded, and this code would build a turn tree in which the *folded*
player acts again, invests again, and can win the pot — with no
exception raised anywhere (`StreetConfig.__post_init__` only checks for
≥2 positions and uniqueness). This would produce **silently wrong
strategies**, not a crash, and there is currently zero multiway test
coverage in `tests/test_chance.py` to catch it. Not reachable through
any shipped endpoint today (all postflop solving is hardcoded
heads-up), but it is a specific, named correctness trap that a "just
try 3 positions" experiment would hit immediately and not necessarily
notice.

### 3.8 [Documentation gap] No caveat anywhere that CFR+ has no equilibrium guarantee at N≥3

CFR/CFR+'s convergence-to-Nash-equilibrium result is a two-player
zero-sum theorem. At N≥3, the average strategies this engine produces
converge at best to a coarse-correlated equilibrium, and need not be a
Nash equilibrium even with exact equities and infinite iterations. This
is standard, well-known game theory, but it appears nowhere in this
project's otherwise extensive documentation of its own approximations
and tradeoffs — worth stating explicitly given this document's whole
premise is "real-time advice for a full table."

### 3.9 [Mechanism note, not a separate bug] Why §3.1's bias doesn't average out with more iterations

CFR+'s regret flooring (`cfr.py:426`, `regret_sum = max(regret_sum +
regret, 0)`) never discounts accumulated positive regret and discards
negative regret entirely. §3.1's phantom +350bb values therefore
*ratchet* rather than get averaged away by running more iterations —
this is mechanistically the same failure shape CLAUDE.md's M8 entry
already diagnosed for the reverted importance-sampling correction, just
with a different root cause. Numerical precision itself is fine
(float64 headroom is nowhere close to being exhausted at any measured
scale) — the degradation is entirely statistical/structural, not
floating-point.

### 3.10 Smaller items worth a line each

- **Suspected, not confirmed:** average-strategy accumulation
  (`cfr.py:427`) doesn't divide by the opponent-reach sampling
  probability `q(I)`, which standard external-sampling MCCFR normally
  does when accumulating the *average strategy* (this is separate from
  the value-side importance-sampling correction M8 already reverted,
  and wouldn't reproduce that failure mode — it's a reweighting bias
  in a plain accumulator, not a regret-poisoning one). Worth checking
  empirically before treating as confirmed.
- Monte Carlo sample count (`MULTIWAY_DEFAULT_SAMPLES=200`) was
  calibrated against a ~2-way coinflip scenario; measured at 7-way it
  produces ~17% relative error run-to-run (vs. ~7% at 2-way) on a
  *cached* value, so the error is frozen per opponent-tuple rather than
  averaging out. No common-random-numbers reuse across the 169
  candidate hands in one vector compounds this further (cheap to fix —
  reuse one board-sample set across the whole vector).
- Three real, currently-dormant thread-safety gaps: an unlocked
  check-then-write race on `equity.py`'s on-disk table cache (real
  today if pre-warming and a request race on a cold start, silent
  corruption of the shared table if hit); `MultiwayEquityCache._cache`
  is an unlocked dict; `InfoSetTable` mutation during solving is an
  unguarded read-modify-write. None of these are live risks under the
  engine's current single-threaded-per-solve usage, but each blocks
  the obvious next scaling move (parallel traversers, a shared warm
  cache across requests) that a real-time full-table product would
  eventually want.
- `node_data.setdefault(id(node), InfoSetTable.zeros(...))` constructs
  fresh zero arrays on *every* visit, including cache hits, because the
  default argument is evaluated eagerly — real allocation churn on the
  hot path at scale, not a correctness issue, but relevant context for
  M17/M18's own "the CFR tensor step isn't the bottleneck" conclusion,
  which was measured with this overhead already included.

### 3.11 What checks out (so this stays a diagnosis, not just a bug list)

- `hand_eval.py`'s vectorized batch evaluator is solid — verified by
  direct reasoning about its tie-break packing (can't overflow between
  category digits) and cross-validated against the scalar reference
  evaluator across thousands of random trials.
- The "no side pots at any N" guarantee (`game_tree.py`) is a genuine,
  provable structural fact, not an assumption — traced directly: a
  raise requires strictly more remaining stack than the amount needed
  to call, and the only all-in size is the full starting stack, so
  nobody can ever raise over an all-in, meaning every live player at a
  real showdown has invested identically. This holds *only* because
  `GameConfig`/`StreetConfig` mandate one shared stack depth — a real,
  hard modeling limit for a live 9-max table (real stacks diverge
  street to street), not a bug, but worth stating plainly.
- The exact-solve dispatch (`solve_preflop`) correctly never routes 3+
  players through the 2-player exact `solve()` path — confirmed no
  test or call site does this.
- The core ES-MCCFR regret update itself (not the averaging question in
  §3.10) is correct as implemented.
- `poker_solver/game_tree.py`'s tree-building is *already* genuinely
  N-player-general — more general than a skeptical reader might assume
  — including the multiway-specific reopened-action-order logic, and
  this is validated by an existing 3-position test, not just asserted.
- `derive_ranges_from_path` (M16) is genuinely N-player-general today,
  with nothing about it that would need to change for multiway
  postflop consumption.

## 4. The structural gap: postflop solving at 3+ live players

**Verdict, stated plainly: this is not a wiring gap like M26 turned out
to be. It is real new engine work, materially larger than any single
milestone shipped so far in this project.**

The chance-node dispatch that chains one street into the next
(`chance.py`, used by every heads-up postflop endpoint since M12) has
**only ever been wired through the exact 2-player `solve()` path.**
`mccfr_solve` — the *only* solving path that can handle 3+ players at
all, since the exact path's payoff representation is an N-dimensional
tensor that's already ~1.6 billion entries at 3-way with a realistic
combo pool — has no `ChanceNode` case in its recursion at all, doesn't
accept a `chance_fn`/`chance_data` parameter, and would raise
`AttributeError` immediately if a chance node were ever handed to it.

Confirmed, independently, four concrete prerequisites, none of which
exist today:

1. **A board-aware N-way equity primitive.** The engine has two equity
   systems today: `MultiwayEquityCache` (N-way, but always deals a
   fresh random 5-card board from nothing — no way to hold a flop/turn
   fixed) and `board_equity.build_board_equity_table` (board-aware, but
   strictly pairwise — one hero vs. one villain). Multiway postflop
   needs the intersection of both properties, and it doesn't exist
   anywhere in the codebase. This is also the expensive part — see §5.
2. **A signature-level change to thread a per-chance-branch equity
   source through MCCFR's terminal-value computation** — the exact
   path already does this (`branch.equity_table` flows through
   `_solve_recurse`'s signature); MCCFR's terminal-value function has
   no equity-table parameter at all today, it reaches for one ambient
   cache.
3. **Per-position range seeding and opponent sampling in `mccfr_solve`.**
   Confirmed: `mccfr_solve` has no `initial_reach` parameter, and
   samples every opponent's hand from the same global preflop-style
   prior regardless of position. Postflop, opponents must be sampled
   from their own *derived* ranges (what they'd actually hold, given
   the action so far) — an algorithmic change, not a keyword argument.
4. **A chance-branch sampling case in `_mccfr_recurse`** — this part,
   on its own, would actually be *cheap*: external-sampling MCCFR
   handles a chance node by sampling one branch instead of averaging
   all ~47, which is a natural fit for the sampling paradigm already in
   use. This is the one piece of the four that really is "just wiring."

On top of the solving-path gap, `solver.py`'s entire `solve_flop*`
family (`solve_flop`, `solve_flop_abstracted`, `solve_flop_turn`,
`solve_flop_to_river`) unconditionally unpacks exactly two positions on
the very first line of each function body, and none of them has any
player-count dispatch at all — unlike `solve_preflop`, which already
branches between the exact and MCCFR paths. Multiway postflop would be
*adding* that dispatch point, not filling one in. And `chance.py`'s own
folded-player bug (§3.7) means even the tree-chaining machinery
underneath needs a real fix, not just a permissive person-count check,
before it's safe at N≥3.

**The one piece of encouraging news, and a genuinely cheap, high-value
near-term target:** the specific case of "N-handed preflop action
folds down to exactly two live players by the time the flop is
reached" is **already almost entirely wired**. `derive_ranges_from_path`
is N-general today and already handles this correctly. `solve_flop`
only needs its normal 2 live positions, which this case naturally
produces. The no-side-pots stack-equality guarantee holds regardless of
how many players started the hand. The only things actually blocking it
are: `library.py`'s `query_strategy_from_path` guard rejecting any
non-2-player-*origin* result outright (its stated justification — that
mapping postflop position labels needs "the full original seating
order" — is over-stated; that order is already sitting on `result.
config.positions`, a closed-form derivation, not genuinely missing
information), and three separate places that each do their own
brittle 2-element unpack of the position tuple instead of sharing one
helper (`library.py`, `solver.py`'s `derive_flop_scenario`, and M26's
own `api/main.py`). This is a real, scoped, ~M22-sized unlock — heads-up
flop *after* a multiway preflop pot, which is the single most common
way a real full-ring hand actually reaches a flop — clearly worth
separating from true 3+-live-player postflop solving, which is the
much bigger piece described above.

## 5. Performance benchmarks

All numbers below are freshly measured this session (not cited from
older CLAUDE.md entries) via the real `TestClient`-exercised endpoints
or direct engine calls, on the current dev machine. Absolute numbers
will vary by hardware; relative shape (what's cheap, what's expensive,
what scales how) is the signal to take away.

### 5.1 Existing live endpoints, current measurements

| Endpoint / scenario | Iterations | Wall-clock | Note |
|---|---:|---:|---|
| `GET /solve/100` (heads-up preflop) | 1,000 | 2.86s | |
| `GET /solve/100?players=3` | 100,000 | 11.05s | |
| `GET /solve/100?players=6` | 30,000 | 96.66s | faster than CLAUDE.md's historical ~2.5min citation — machine-load variance, not a regression |
| `GET /solve/100?players=9` | 300 | 93.07s | **produces the biased output in §3.1** |
| `GET /solve_flop` (heads-up demo) | 1,000 | 2.41s | |
| `GET /solve_flop_turn` (heads-up demo, miss) | 200 | 15.97s | |
| `GET /solve_flop_to_river` (heads-up demo, miss) | 20 | 53.68s | |
| `GET /solve_flop_cached` (miss) | — | 1.39s | |
| `GET /solve_flop_cached` (repeat, hit) | — | 0.0002s | |
| `POST /solve_turn_from_path` (miss) | 200 | 38.25s | |
| `POST /solve_turn_from_path` (repeat, hit) | — | 0.0003s | |

### 5.2 What a board-aware N-way equity primitive would cost (doesn't exist today — §4, item 1)

A standalone, minimal prototype (not committed — reuses the project's
own vectorized `hand_eval.best_hand_rank_batch`, the same evaluator
`board_equity.py` already trusts, so this measures the missing
*dealing/aggregation* layer specifically, not hand-ranking cost): one
fixed hand per player, a fixed 3-card board, Monte Carlo the rest,
batch-evaluate everyone's best hand per sample, aggregate win shares.

| Players | 500 samples | 2,000 samples |
|---|---:|---:|
| 2 | 14.75ms | 57.53ms |
| 3 | 20.83ms | 88.93ms |
| 6 | 42.64ms | 185.11ms |
| 9 | 73.02ms | 286.46ms |

This scales roughly linearly with player count per sample batch — the
vectorized evaluator handles the added players cheaply. **This number
is a naive lower-bound-ish baseline, not an optimized estimate**: it
re-deals and re-evaluates every player fresh per call, rather than
reusing the "deal fixed opponents once, vary only the traverser's
cards" optimization `MultiwayEquityCache.traverser_equity_vector`
already applies for the boardless (preflop) case. A real
implementation would very likely beat these numbers per call.

The real cost driver is not one call — it's how many *distinct*
opponent-hand combinations a solve needs across many candidate hands
and many MCCFR iterations. `MultiwayEquityCache`'s own memoization
model (one cache entry per distinct opponent-hand tuple) is the
existing precedent for how this would scale: at realistic 3-way postflop
combo-range sizes (~200 combos/side), that's on the order of **~40,000
distinct opponent-hand-pair combinations**, each needing its own
Monte Carlo run — versus a handful of distinct preflop opponent-class
tuples today. `board_equity.py`'s own existing pairwise (2-player)
measurement already puts a 78-combo range's full equity table at ~19s;
a genuinely N-way, board-aware, per-chance-branch version of that,
called once per distinct flop terminal reached during a real MCCFR
solve, is the piece of work most likely to dominate total cost for
multiway postflop — consistent with M17/M18's own finding that
equity-table construction, not the CFR tensor step, is where cost
concentrates even in the two-player case.

### 5.3 Net read on performance

Nothing here suggests full-table, every-street solving is impossible —
the tree-building and range-derivation layers are already
demonstrated-general and cheap (§3.11). But the *equity* layer, which
already dominates cost in the two-player case, does not exist yet in
the N-way-and-board-aware shape multiway postflop needs, and every
signal (the existing `MultiwayEquityCache` cache-hit collapse at high
N, the pairwise 2-player board-equity cost curve, this session's own
naive-baseline probe) points at it being the dominant cost once built —
likely the same "equity construction dominates, not the solver" shape
M17/M18 already found for heads-up, just with a much larger
combinatorial base at N≥3.

## 6. Prioritized recommendations

Ranked by (severity × how cheap the fix looks), not by effort to build
new capability — the point of this document is to make that call
explicit later, not here.

1. **Fix §3.1's placeholder** (`0.5` → `1/n_live`) and **add an O(N)
   feasibility precheck before `deal_n_hands`'s exponential backtrack**
   (§3.2). Both are small, localized changes to `poker_solver/equity.py`
   with a demonstrated, dramatic effect on multiway preflop output
   correctness and 9-max latency. This is the highest-value, lowest-risk
   item on this list — it doesn't require deciding anything about the
   full-table-postflop question at all, just correcting existing,
   already-shipped preflop behavior.
2. **Close the coverage gap in §3.3** — at minimum, a test that
   exercises `MultiwayEquityCache` with 3+ opponents (so the
   whole-vector fallback is reachable at all), and some way to surface
   visit counts / a confidence signal alongside a strategy so "genuinely
   converged" and "never sampled" are distinguishable in the API
   response, not just internally.
3. **The card-removal-aware opponent sampler** (§3.4) — the root cause
   behind both #1 items above; likely the more durable fix once #1's
   quick patches are in, and directly relevant to real future MCCFR
   work either way (§4, prerequisite 3 needs range-aware sampling
   regardless).
4. **The heads-up-flop-after-multiway-preflop unlock** (§4's "one piece
   of encouraging news") — real user value (the modal way a full-ring
   hand reaches a flop), and by the audit's own tracing, close to
   already-wired: drop one overly-strict guard, consolidate three
   duplicated position-unpacking call sites into one shared helper.
   Worth scoping as its own milestone independent of the much larger
   item below.
5. **True multiway (3+ live) postflop solving** — real, substantial new
   engine work per §4: a board-aware N-way equity primitive (new
   module), a signature-level MCCFR refactor to carry per-branch equity
   sources, range-aware opponent sampling, a chance-branch case in
   `_mccfr_recurse`, and closing `chance.py`'s folded-player bug (§3.7)
   before it's reachable at all safely. Should not be scoped until #1-3
   are addressed, since they change what "correct" looks like for
   whatever gets built.
6. **Documentation**: add the missing N≥3 Nash-guarantee caveat (§3.8)
   wherever this project documents its own approximations; correct the
   9-max iteration-budget root-cause attribution CLAUDE.md currently
   states (cache-hit collapse) to reflect what's actually measured
   (§3.2's backtracking stall).
7. Lower priority, same list either way: suit-assignment bias (§3.5),
   the determinism-claim fix (§3.6), the three thread-safety gaps
   (§3.10) — none live risks today, all real blockers for a future
   concurrent/scaled deployment.
