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
isn't viable for an interactive endpoint. M67 accepted that cost rather
than the coverage gap it bought: cfg.MULTIWAY_PREFLOP_HANDS is now the
full 169-class pool (~170s at 6-max, cached per spot and pre-warmed at
stack 100), because the previous 8-class subset meant no advice at all
for ~95% of starting hands. This is a real, documented scope limit, not a
hidden shortcut: it demonstrates the N-player-general engine, not a
production-grade multiway range chart.

Per-table-size iteration budgets (cfg.MULTIWAY_TABLE_CONFIGS) shrink as
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
Net effect: 3-max reaches good convergence in minutes; 9-max is
deliberately budgeted fewer iterations and correspondingly noisier —
documented, not hidden, the same way M8 documented 3-max's own limits.

**Correction from M27**: this docstring used to also claim 6-max
"reaches good convergence in minutes" at 30,000 iterations — that
claim didn't hold up under closer testing. Fixing a real equity bug
(poker_solver/equity.py's MultiwayEquityCache — see its own module
comment) surfaced a separate, pre-existing MCCFR convergence
sensitivity at 6-max with this project's small, top-heavy demo hand
pool: some hands' learned fold rate grows steadily with more
iterations instead of stabilizing (confirmed to also affect the
*pre-fix* code, just biased in the opposite direction, so this isn't
something the equity fix introduced). 6-max's own iteration budget
below was cut sharply as a result, mirroring 9-max's own "smaller,
deliberately conservative" precedent rather than the fuller
convergence 30,000 iterations was believed to reach — see CLAUDE.md's
M27 entry for the full investigation. Fully resolving 6-max's
convergence is real, separate future work, not attempted here.

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
cfg.MULTIWAY_PREFLOP_HANDS does — a flop combo range has to exclude whatever
the board itself blocks, which varies per request. So the curated input
here (cfg.DEMO_FLOP_HERO_CLASSES/cfg.DEMO_FLOP_VILLAIN_CLASSES) is one level up:
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
smaller, separate curated pool instead (cfg.DEMO_CHAINED_FLOP_HERO_CLASSES/
cfg.DEMO_CHAINED_FLOP_VILLAIN_CLASSES, shared between both endpoints so a
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
to generalize, cfg.MAX_FLOP_TO_RIVER_ITERATIONS is set equal to its own
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
`_get_or_solve_preflop_raw`) through `derive_ranges_from_path` (M16) into `query_
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
cfg.MAX_PATH_QUERY_CLASSES_PER_SIDE highest-frequency classes per side at
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
over**: an early version reused `cfg.MAX_PATH_QUERY_CLASSES_PER_SIDE`
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
convergence 10x below every sibling endpoint's own cfg.MAX_ITERATIONS for a
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

GET /solve_flop_multiway and GET /solve_flop_turn_multiway are M37's
deliverable: the first live endpoints wiring up true multiway (3+ live
position) postflop solving (M30-M36) — every prior postflop endpoint in
this file, however deep the runout, has been 2-position (OOP/IP) end to
end. Same board/pot/stack-in, one-position's-strategy-out shape as
every other /solve_flop* endpoint (reuses FlopSolveResponse/format_flop_
response unchanged — both were already position-count-agnostic, per
strategy_format.py's own docstring, which had anticipated this exact
gap since M14), but `position` now accepts OOP, MID, *or* IP, and the
response's own `positions` field carries all 3 rather than 2. Backed by
solve_flop_multiway (M35) and solve_flop_turn_multiway (M36) —
cfr.mccfr_solve + multiway_board_equity.NwayBoardEquityCache instead of
cfr.solve + board_equity.build_board_equity_table, over a curated
cfg.DEMO_MULTIWAY_FLOP_CLASSES pool (one suited class per position, 11
combos total after board-legal expansion). Deliberately 3-max only —
see cfg.DEMO_MULTIWAY_FLOP_CLASSES' own comment for why 6-max/9-max
multiway postflop isn't scoped here.

Both endpoints turned out to be genuinely *cheap* by this file's own
established standards — measured live, at this endpoint's own 11-combo
pool, `max_raises=2` (one real sized bet + all-in): solve_flop_multiway
~3.0-3.5s, close to flat across iteration count (200 vs 2000 iterations
measured ~3.0s vs ~3.5s — the equity cache saturates fast at this small
a pool, the same "flat cost" shape solve_flop_turn's own module-
docstring paragraph already established, for the same underlying
reason). solve_flop_turn_multiway ~1.3-13.8s depending on iteration
count (50 iters ~1.3s, 200 iters ~5.8s, 500 iters ~13.8s) — genuinely
NOT flat, unlike its 2-position cousin solve_flop_turn: every iteration
can sample a new (terminal, card) pair, a materially bigger space at
this pool size than the flop-level equity cache's own opponent-tuple
space, so cfg.MAX_FLOP_TURN_MULTIWAY_ITERATIONS is set far more
conservatively than cfg.MAX_FLOP_MULTIWAY_ITERATIONS's own generous 10x-
default headroom. Neither endpoint is pre-warmed — both are cheaper
than /solve_flop's own already-"not worth pre-warming" ~2.6s at their
respective defaults, so a cold-start tax was never the concern
/solve_flop_turn's/`/solve_flop_to_river`'s own pre-warming exists to
avoid.

GET /solve_flop_to_river_multiway is M40's deliverable: the same 3-max
multiway flop as /solve_flop_multiway/`/solve_flop_turn_multiway`,
chained all the way to a real multiway river decision — wiring up M39's
solve_flop_to_river_multiway, the direct N-position generalization of
`/solve_flop_to_river`. Own cache dict (_flop_to_river_multiway_cache),
same reasoning as every other endpoint's own dict.

Measured live, at the same 11-combo cfg.DEMO_MULTIWAY_FLOP_CLASSES pool:
genuinely *cheaper* than solve_flop_turn_multiway at every iteration
count compared (200 iters ~3.89s vs. ~5.8s; 500 iters, the most
expensive point measured, ~9.54s vs. ~13.8s) — the OPPOSITE of what
the 2-position solve_flop_to_river/`solve_flop_turn` pair found (M13/
M14, where the second hop cost dramatically more). Traced to two
independent reasons (see CLAUDE.md's M39 entry for the full
explanation): build_mccfr_chance_branch's lazy, one-sampled-card-at-a-
time design never pays build_chance_node's own eager combinatorial
cost, and a complete-river-board equity lookup needs no enumeration at
all, cheaper still than a turn-level lookup's own already-cheap exact
enumeration. cfg.MAX_FLOP_TO_RIVER_MULTIWAY_ITERATIONS is therefore set
equal to solve_flop_turn_multiway's own default/cap (50/500), not
solve_flop_to_river's own tiny 2-position ones. Not pre-warmed, for the
same reason as the two endpoints above.

POST /solve_flop_multiway_from_path is M42's deliverable, closing the
project's own long-named-open gap between derive_ranges_from_path's
already-N-general output (M16) and a live multiway postflop endpoint —
the multiway analog of /solve_flop_from_path (M24), for the case that
endpoint structurally can't serve: a real action path leaving 3+ live
positions at the flop, not just 2. Requires a genuine 3+-live-position
terminal; a 2-survivor path 422s with a message pointing at
/solve_flop_from_path instead (which uses the exact, not MCCFR-
approximate, 2-position solver — genuinely better for that case, not
just a narrower one). Calls solve_flop_multiway (M35) directly, not
query_strategy_from_path (M23) — that function's own canonical-library
machinery (query_strategy -> solve_flop -> build_board_equity_table) is
2-position all the way down, so this endpoint instead uses its own
plain, unpartitioned dict cache (_flop_multiway_path_cache), keyed on
everything the derived situation and the solve actually depend on
(action_path, players, stack_bb, board, both iteration counts).

Two independent iteration fields, same TurnPathRequest-style split as
/solve_turn_from_path: `iterations` (the preflop leg — inert whenever
`players != 2`, per _get_or_solve_preflop_raw's own established
behavior) and `flop_iterations` (this endpoint's own real cost driver).

A real, load-bearing consequence of reusing _get_or_solve_preflop_raw
unchanged: whenever `players != 2`, the preflop leg is already
restricted to whatever cfg.MULTIWAY_PREFLOP_HANDS holds. That USED to be
a small 8-class pool, far narrower than the 169 classes /solve_flop_from_
path solves over at players=2; M67 made the two identical, so this
endpoint's own class cap now genuinely binds — see this endpoint's own
cap constant for the re-measurement that followed — and its own class cap
(cfg.MAX_MULTIWAY_PATH_QUERY_CLASSES_PER_POSITION) only ever ranks among
those same 8 classes, not 169. Measured for real anyway, since solve_
flop_multiway's own cost curve is far steeper than solve_flop's at any
pool size (M35's own finding: pool size is the dominant cost driver,
compounded by MCCFR's opponent-sampling cache-miss rate): at a real
3-max open/call/call path reaching a genuine 3-live-position flop,
solve_flop_multiway's own default (200) iterations — cap=1 -> 18
combos, ~3.33s; cap=2 -> 35 combos, ~22.46s; cap=3 -> 62 combos,
~46.63s. Set to 2, landing in the same "tolerable for a live request"
bracket /solve_flop_from_path's own ~17-21s already established.
Iteration-count scaling at this cap's own 35-combo pool is NOT close to
flat, unlike cfg.DEMO_MULTIWAY_FLOP_CLASSES' own tiny 11-combo pool: 200
iters ~22.46s, 500 iters ~36.76s, 1000 iters ~48.20s, 2000 iters
~58.13s. cfg.MAX_MULTIWAY_PATH_QUERY_FLOP_ITERATIONS is therefore set to
500 (~37s), not solve_flop_multiway's own generous 2000-iteration
ceiling (tuned against a much smaller pool).

Not pre-warmed — the whole point is a real, client-supplied situation,
the same reasoning /solve_flop_from_path's own module-docstring
paragraph already established.

POST /solve_turn_multiway_from_path is M44's deliverable, closing the
"turn-depth" item M42/M43 both left open — the multiway analog of
/solve_turn_from_path (M26), for a real preflop path that leaves 3+
live positions at the flop. Requires a genuine 3+-live-position
terminal; a 2-survivor path 422s with a message pointing at
/solve_turn_from_path instead — the same scope boundary /solve_flop_
multiway_from_path already established relative to /solve_flop_from_
path. Uses `flop_iterations`, not a separate `turn_iterations` field —
solve_flop_turn_multiway's own single-solve design (M36) produces both
the flop and turn strategies from ONE call, unlike the exact 2-position
solver's own two-stage cost profile.

A real, structural gap this endpoint had to solve that /solve_turn_
from_path never faced: solve_flop_turn_multiway's chance_data only
contains the (terminal, card) pairs MCCFR actually happened to sample
during solving (see that function's own docstring), not every legal
next card the way the exact solver's chance_data does — a real turn
card a client asks about can easily be one MCCFR never sampled,
especially spread across a derived pool's many distinct terminals at a
modest iteration budget. Fixed with poker_solver.solver.ensure_flop_
turn_multiway_branch (M44): on a chance_data miss for a legal card, it
builds and caches exactly the branch MCCFR would have built had it
sampled that pair itself (chance.build_mccfr_chance_branch is a pure
function of its inputs, proven deterministic in M32's own tests). The
freshly-built branch's own strategy correctly reports `trained=False`
for every hand via StrategyResult.strategy_at/.trained_hands's own
EXISTING "no node_data entry -> uniform fallback" behavior (M28) — no
new signal needed, that mechanism already covers this case honestly.

cfg.MAX_MULTIWAY_TURN_PATH_QUERY_CLASSES_PER_POSITION ended up landing on
the SAME value (2) as the flop-only endpoint's own class cap, unlike
M26's own precedent where the turn-level cap had to shrink relative to
the flop-level one — measured for real (see that constant's own
comment): solve_flop_turn_multiway's chance dispatch is cheap enough at
this pool size (lazy, one-sampled-card-at-a-time construction, not the
exact solver's eager combinatorial cost) that the same class count
stays affordable. Own cache dict (_turn_multiway_path_cache), keyed
only on the preflop leg (not flop_action_path/turn_card, resolved by
walking the already-solved tree afterward, same "resolving is free,
re-solving isn't" reasoning _turn_path_cache's own M26 key already
established) — same lock also guards ensure_flop_turn_multiway_
branch's own in-place chance_data mutation.

POST /solve_river_from_path (M46) closes the last street this project's
real-action-path thread had left uncovered: one hop further than
/solve_turn_from_path, via solve_flop_to_river (M13) instead of
solve_flop_turn. Unlike the turn endpoint (which deliberately exposes
only the FIRST turn decision, never a deeper turn-street path), a real
river decision needs a real TURN action path too, since the turn is
itself a full betting round — this endpoint's request adds that field,
plus a real dealt river card.

Measured before shipping, and the real finding here: solve_flop_to_
river's cost is dominated by combo-POOL SIZE far more steeply than any
other path-derived endpoint in this file, to the point that even a
single CLASS-level cap (the same lever every sibling endpoint uses) is
already too coarse — a single class can expand to up to 12 combos.
cfg.RIVER_PATH_QUERY_MAX_COMBOS_PER_SIDE (own comment has the full, twice-
re-measured numbers) was set to 3 combos/side at M46, then doubled to 6
at M49 once M48's ~5-6x hand-evaluator speedup made the same wall-clock
budget afford a meaningfully wider real range. river_iterations' own
cap mirrors cfg.MAX_FLOP_TO_RIVER_ITERATIONS' own "==default, zero
headroom" discipline, for the identical reason: cost at this scale is
already at the outer edge of tolerable at the default alone.

POST /advise (M51) is the unified front door over all of the above: one
request describing a whole real situation (stack, table size, the
preflop action path, and — as the hand progresses — a board, flop
action, turn card, turn action, river card), one response with advice
for the decision actually faced. Street depth is INFERRED from which
fields are present rather than client-declared, so there's no second
source of truth to disagree with the card/action fields; _infer_street
rejects every partial or skipped combination (a river card with no turn
card, a turn card with no flop action, and so on).

Each (street, table size) cell delegates to whichever sibling
orchestrator already serves it — this is a front door, not a second
implementation to keep in sync — so every cell keeps its own cache, its
own separately-measured cap constant, and its own solver choice.
_ADVISE_ITERATION_CAPS maps each cell to that sibling's own default/max
rather than inventing one blended value.

Two things no sibling endpoint offers:

  * `hero_cards` — advice for YOUR specific hand. Force-included in
    every live position's derived range BEFORE the cap is applied (and,
    for the library-backed cell, at class level in capped_scenario too,
    per library.query_strategy_from_path's own class-dicts-only
    contract). Without that, a hand outside the top-K would be silently
    absent from the very solve meant to advise it — exactly the
    marginal case advice matters most for. `hero.in_range` reports
    honestly whether it survived the cap on its own weight. NOTE the
    real cost: force-inclusion adds at most one combo per live
    position, which is a genuine (if small) solve-cost increase.
  * `source` — names which backend answered ("exact", "mccfr",
    "library_hit", "library_miss", "preflop"). This makes the family's
    one real asymmetry visible instead of hidden: the heads-up-flop
    cell goes through the canonical library, which persists only a
    flattened strategy dict and so returns `trained: null` (an explicit
    null, not a silently-omitted field — M28's documented boundary).
    Kept rather than dropped for uniformity because the library's own
    hit is ~0.2ms against a ~20s miss; trading that for a tidier table
    would be a bad deal, but hiding it would be worse.

As of M53 the (street x table size) matrix is COMPLETE — the last cell,
("river", multiway), is served by the same generalized multiway walker
the turn cell uses, one hop deeper. M44 had left open whether a SECOND
chained chance-hop needs structurally different treatment; it does not
(see solver.ensure_mccfr_chance_branch, renamed from M44's own
flop_turn-specific name once that was proven). _ADVISE_UNSUPPORTED_CELLS
is now empty, kept as the declared place any future unsupported cell
would state its real reason.

/advise also adds the one street no path-derived endpoint ever served:
PREFLOP advice, read straight off the cached preflop solve at whatever
node the action path reaches. Note its deliberately INVERTED terminal
requirement — every postflop cell needs the preflop action to have
CLOSED before a board is dealt, whereas preflop advice needs it still
open, with someone left to act.

Pre-warmed, unlike every other path-derived endpoint in this file — its
own cost (~43s at default) is a meaningfully worse cold-start tax than
any of them, the same "worth pre-warming" bar solve_flop_turn/solve_
flop_to_river's own fixed-demo pre-warms were held to at M14.
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
from poker_solver.hand_strength import strength_percentile
from poker_solver.combos import HandCombo, range_from_class_frequencies
from poker_solver.equity import MultiwayEquityCache
from poker_solver.game_tree import (
    CALL_OR_CHECK,
    DecisionNode,
    GameConfig,
    TerminalNode,
    postflop_action_order,
    resolve_action,
)
from poker_solver.library import query_strategy, query_strategy_from_path
from poker_solver.solver import (
    DEFAULT_FLOP_ITERATIONS,
    DEFAULT_FLOP_MULTIWAY_ITERATIONS,
    DEFAULT_FLOP_TO_RIVER_ITERATIONS,
    DEFAULT_FLOP_TO_RIVER_MULTIWAY_ITERATIONS,
    DEFAULT_FLOP_TURN_ITERATIONS,
    DEFAULT_FLOP_TURN_MULTIWAY_ITERATIONS,
    DEFAULT_ITERATIONS,
    StrategyResult,
    derive_ranges_from_path,
    ensure_mccfr_chance_branch,
    solve_flop,
    solve_flop_multiway,
    solve_flop_to_river,
    solve_flop_to_river_multiway,
    solve_flop_turn,
    solve_flop_turn_multiway,
    solve_preflop,
)
from poker_solver.starting_hands import StartingHand
from poker_solver.strategy_format import format_flop_response, format_solve_response

from .schemas import (
    ActionPathRequest,
    AdviseRequest,
    AdviseResponse,
    EquityResponse,
    FlopMultiwayPathQueryResponse,
    FlopPathQueryResponse,
    FlopQueryResponse,
    FlopSolveResponse,
    MultiwayFlopPathRequest,
    MultiwayTurnPathRequest,
    PreflopWalkRequest,
    PreflopWalkResponse,
    RiverPathQueryResponse,
    RiverPathRequest,
    SolveResponse,
    TurnMultiwayPathQueryResponse,
    TurnPathQueryResponse,
    TurnPathRequest,
)


logger = logging.getLogger("poker_solver.api")

# M61: the cache layer (see caches.py). _SolveCache is imported too —
# tests call _SolveCache.clear_all() through this module.
# M62: constants are read as `config.X` at call time, never copied into
# this module's namespace. One canonical location per value means a test
# monkeypatching `api.config.X` affects every reader — routes here AND
# orchestrators in solving.py — instead of only whichever module happened
# to hold the copy. That shadow-copy hazard is exactly what M61 had to
# work around by keeping the orchestrators here.
# Aliased to `cfg`: `config` is a very common LOCAL name in this
# codebase (`config = GameConfig(...)`, `config = StreetConfig(...)`),
# and a module-level `config` would be silently shadowed inside exactly
# those functions — it was, and surfaced as an UnboundLocalError.
from . import config as cfg
from .caches import (
    _SolveCache,
    _flop_cache,
    _flop_multiway_cache,
    _flop_multiway_path_cache,
    _flop_query_library,
    _flop_to_river_cache,
    _flop_to_river_multiway_cache,
    _flop_turn_cache,
    _flop_turn_multiway_cache,
    _flop_node_cache,
    _multiway_cache,
    _multiway_equity_caches,
    _path_query_libraries,
    _preflop_raw_cache,
    _river_path_cache,
    _turn_multiway_path_cache,
    _turn_path_cache,
)



def _prewarm_enabled() -> bool:
    """Off switch for tests (and anyone embedding the app) — avoids
    paying for a handful of full solves just to start the app up."""
    return os.environ.get("POKER_SOLVER_PREWARM", "1") != "0"


# M62: the solving layer (see solving.py) — every _get_or_solve_*/
# _query_*/_advise* orchestrator. Imported by name so this module's
# routes and pre-warm read exactly as before.
from .solving import (
    _ADVISE_ITERATION_CAPS,
    _ADVISE_STREETS,
    _ADVISE_UNSUPPORTED_CELLS,
    _PathSituation,
    _advise,
    _advise_preflop,
    _cache_key,
    _cap_range,
    _cap_range_to_combos,
    _combo_to_class,
    _derive_path_situation,
    _get_or_solve_flop,
    _get_or_solve_flop_multiway,
    _get_or_solve_flop_to_river,
    _get_or_solve_flop_to_river_multiway,
    _get_or_solve_flop_turn,
    _get_or_solve_flop_turn_multiway,
    _get_or_solve_multiway,
    _get_or_solve_preflop_raw,
    _infer_street,
    _live_position_count,
    _preflop_walk,
    _query_flop,
    _query_flop_from_path,
    _query_flop_multiway_from_path,
    _query_river_from_path,
    _query_turn_from_path,
    _query_turn_multiway_from_path,
    _range_confidence,
    _resolve_action_path,
)


# M124 (D2). The pre-warm is the only thing standing between a user and
# a 66-93s cold multiway preflop solve, and until now it was invisible:
# seven duplicated `except Exception: logger.exception(...)` blocks, a
# daemon thread nobody joined, and zero test coverage — the largest
# uncovered block in the project. A config typo or a renamed helper
# would not have failed anything; it would have looked like "the product
# is slow", with a stack trace in a log nobody reads.
#
# This record makes the outcome inspectable and, more to the point,
# ASSERTABLE. Same failure shape as F25 (M107), where nothing verified
# the app was served at all.
PREWARM_STATUS: dict = {"started": False, "finished": False, "steps": []}


def _prewarm_step(name: str, warm) -> bool:
    """Run one pre-warm step, recording its outcome either way.

    Still swallows the exception — one unavailable spot must not stop the
    other warms, which is why the original wrote it this way. The change
    is that the failure is now recorded rather than only logged.
    """
    logger.info("pre-warming %s", name)
    try:
        warm()
    except Exception as exc:
        logger.exception("pre-warm failed for %s", name)
        PREWARM_STATUS["steps"].append({"name": name, "ok": False,
                                        "error": f"{type(exc).__name__}: {exc}"})
        return False
    PREWARM_STATUS["steps"].append({"name": name, "ok": True, "error": None})
    return True


def _prewarm_common_depths() -> None:
    PREWARM_STATUS["started"] = True
    PREWARM_STATUS["finished"] = False
    PREWARM_STATUS["steps"] = []

    for depth in cfg.PREWARM_STACK_DEPTHS:
        _prewarm_step(
            f"preflop stack_bb={depth}",
            lambda d=depth: _get_or_solve_preflop_raw(d, DEFAULT_ITERATIONS),
        )

    # M76: every (table size, depth) pair, not just stack_bb=100 — see
    # cfg.MULTIWAY_PREWARM_STACK_DEPTHS for why the depth list is
    # separate from the heads-up one and why these three depths.
    for players in cfg.MULTIWAY_TABLE_CONFIGS:
        for depth in cfg.MULTIWAY_PREWARM_STACK_DEPTHS:
            _prewarm_step(
                f"{players}-max stack_bb={depth}",
                lambda p=players, d=depth: _get_or_solve_multiway(d, p),
            )

    # solve_flop itself (~2.6s) isn't worth pre-warming — /solve_flop
    # was never given this treatment, since a couple seconds is a fine
    # cold-start tax. solve_flop_turn/solve_flop_to_river are meaningfully
    # slower (~26s/~63s), so pre-warm one instance of each against the
    # frontend's own default board/pot/stack (FlopSolver.tsx's
    # DEFAULT_BOARD/DEFAULT_POT/DEFAULT_STACK_BB — keep these two in sync
    # if either side's defaults ever change) so a user's very first,
    # overwhelmingly-likely-unmodified click is instant rather than
    # paying the full cost live.
    _prewarm_step(
        "solve_flop_turn default board",
        lambda: _get_or_solve_flop_turn(
            tuple(parse_cards(cfg.DEFAULT_CHAINED_FLOP_BOARD)), 10.0, 40.0,
            DEFAULT_FLOP_TURN_ITERATIONS,
        ),
    )
    _prewarm_step(
        "solve_flop_to_river default board",
        lambda: _get_or_solve_flop_to_river(
            tuple(parse_cards(cfg.DEFAULT_CHAINED_FLOP_BOARD)), 10.0, 40.0,
            DEFAULT_FLOP_TO_RIVER_ITERATIONS,
        ),
    )

    # **M133, worth knowing before spending anything more here.** These
    # last two steps warm DEPRECATED routes. `/solve_turn_from_path` and
    # `/solve_river_from_path` were superseded by `/advise`, and their
    # frontend clients have **zero non-test callers** — checked against
    # `frontend/src/api.ts`, where `fetchTurnStrategyFromPath`,
    # `fetchRiverStrategyFromPath`, `fetchFlopStrategyFromPath` and
    # `fetchMultiwayFlopStrategyFromPath` are all defined and none is
    # used outside tests. The comments below still name a
    # `TurnPathSolver.tsx` that no longer exists.
    #
    # They are kept because the routes are still public and an external
    # caller may use them — but they cost ~60s of a 510s startup for
    # traffic the UI does not generate. **The high-value replacement is
    # not obvious**: /advise's own postflop cost is board-specific, and a
    # board cannot be usefully guessed, which is the same wall the
    # precompute idea runs into.
    #
    # /solve_turn_from_path's own cost (~16-26s) is in the same
    # tax-worth-avoiding bracket the two pre-warms above were already
    # accepted for. Matches TurnPathSolver.tsx's own default preflop/
    # flop presets and board — keep these in sync if either side's
    # defaults ever change (same "kept in sync manually" precedent
    # cfg.DEFAULT_CHAINED_FLOP_BOARD's own comment already accepts).
    _prewarm_step(
        "solve_turn_from_path default line",
        lambda: _query_turn_from_path(
            ["raise", "call_or_check"], ["raise", "call_or_check"],
            parse_cards("2h")[0], 100.0,
            tuple(parse_cards(cfg.DEFAULT_CHAINED_FLOP_BOARD)),
            DEFAULT_ITERATIONS, DEFAULT_FLOP_TURN_ITERATIONS,
        ),
    )

    # /solve_river_from_path's own cost (~43s at its own default combo
    # cap) is well past the tax-worth-avoiding bracket the pre-warms
    # above were already accepted for — a real, worse cold-start tax
    # than any of them.
    #
    # **M133: the flop leg is check/check, not bet/call, and the old line
    # had NEVER worked.** It asked for `["raise", "call_or_check"]` on the
    # flop, but this endpoint's tree runs at FLOP_TO_RIVER_MAX_RAISES=1
    # with empty FLOP_TO_RIVER_RAISE_SIZES, so `_build` offers no sized
    # raise at all — only call_or_check and all_in. Every attempt raised
    # "step 0: 'raise' is not legal at this node", so this pre-warm has
    # been failing since it was added and every default river request has
    # paid the full cold cost.
    #
    # It was invisible until M124 made the pre-warm record its outcomes;
    # the step swallowed its own exception into a log line before that.
    # Check/check keeps both positions live without an all-in, which is
    # what the original comment wanted and what this tree can express.
    _prewarm_step(
        "solve_river_from_path default line",
        lambda: _query_river_from_path(
            ["raise", "call_or_check"], ["call_or_check", "call_or_check"],
            parse_cards("2h")[0], ["call_or_check", "call_or_check"],
            parse_cards("9s")[0], 100.0,
            tuple(parse_cards(cfg.DEFAULT_CHAINED_FLOP_BOARD)),
            DEFAULT_ITERATIONS, cfg.DEFAULT_RIVER_PATH_QUERY_ITERATIONS,
        ),
    )

    PREWARM_STATUS["finished"] = True
    failed = [step["name"] for step in PREWARM_STATUS["steps"] if not step["ok"]]
    if failed:
        logger.error("pre-warm finished with %d failed step(s): %s", len(failed), failed)
    else:
        logger.info("pre-warm finished: %d step(s), all ok", len(PREWARM_STATUS["steps"]))


@asynccontextmanager
async def lifespan(app: FastAPI):
    if _prewarm_enabled():
        threading.Thread(target=_prewarm_common_depths, daemon=True).start()
    yield


app = FastAPI(title="Poker Solver API", lifespan=lifespan)


@app.get("/solve/{stack_bb}", response_model=SolveResponse)
async def solve(
    stack_bb: float = Path(..., gt=0, description="Effective stack depth, in big blinds"),
    iterations: int = Query(DEFAULT_ITERATIONS, gt=0, le=cfg.MAX_ITERATIONS, description="Heads-up only"),
    players: int = Query(2, description="2 (heads-up), or 3/6/9 for a multiway demo"),
    position: str | None = Query(None, description="Which position's strategy to return"),
):
    if players != 2 and players not in cfg.MULTIWAY_TABLE_CONFIGS:
        valid = ", ".join(str(p) for p in [2, *cfg.MULTIWAY_TABLE_CONFIGS])
        raise HTTPException(status_code=422, detail=f"players must be one of {valid}")
    try:
        # One helper for every table size (M58). _get_or_solve_preflop_raw
        # already delegates to _get_or_solve_multiway when players != 2,
        # so this is genuinely one cache, not a branch that happens to
        # look unified — and formatting here rather than inside a second
        # cache is what lets `position` be honored at EVERY table size.
        # It previously was not: heads-up silently returned first-to-act
        # regardless of what the caller asked for.
        result = await run_in_threadpool(_get_or_solve_preflop_raw, stack_bb, iterations, players)
        body = format_solve_response(result, position=position)
        # M125 (E2): the same confidence signals /advise carries, from
        # the same constants. This endpoint serves the 9-max range chart,
        # which CLAUDE.md says must not be presented as authoritative,
        # and it used to say nothing at all. Preflop by definition here,
        # so the sizing caveat applies whenever the table size does.
        body["solver_confidence"] = (
            "low" if players in cfg.LOW_CONFIDENCE_TABLE_SIZES else "high"
        )
        body["solver_confidence_reason"] = cfg.LOW_CONFIDENCE_TABLE_SIZES.get(players)
        sizing_low = players in cfg.SIZING_CAVEAT_TABLE_SIZES
        body["sizing_confidence"] = "low" if sizing_low else "high"
        body["sizing_confidence_reason"] = cfg.SIZING_CAVEAT_REASON if sizing_low else None
        return body
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
    iterations: int = Query(DEFAULT_FLOP_ITERATIONS, gt=0, le=cfg.MAX_FLOP_ITERATIONS),
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
    iterations: int = Query(DEFAULT_FLOP_TURN_ITERATIONS, gt=0, le=cfg.MAX_FLOP_TURN_ITERATIONS),
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
    iterations: int = Query(DEFAULT_FLOP_TO_RIVER_ITERATIONS, gt=0, le=cfg.MAX_FLOP_TO_RIVER_ITERATIONS),
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


@app.get("/solve_flop_multiway", response_model=FlopSolveResponse)
async def solve_flop_multiway_endpoint(
    board: str = Query(..., description="Exactly 3 cards, e.g. Jh7d2c"),
    pot: float = Query(10.0, gt=0, description="Pot entering the flop"),
    stack_bb: float = Query(40.0, gt=0, description="Effective stack behind, in big blinds"),
    iterations: int = Query(DEFAULT_FLOP_MULTIWAY_ITERATIONS, gt=0, le=cfg.MAX_FLOP_MULTIWAY_ITERATIONS),
    position: str | None = Query(None, description="OOP, MID, or IP — defaults to OOP, the first to act"),
):
    """A real 3-max multiway flop (M37, wiring up M35's solve_flop_
    multiway) — runouts beyond the flop are averaged inside
    NwayBoardEquityCache itself, not chained into a real turn decision
    (see /solve_flop_turn_multiway for that)."""
    try:
        board_cards = tuple(parse_cards(board))
        if len(board_cards) != 3:
            raise ValueError(f"board must have exactly 3 cards for a flop, got {len(board_cards)}")
        result = await run_in_threadpool(_get_or_solve_flop_multiway, board_cards, pot, stack_bb, iterations)
        return format_flop_response(result, board="".join(str(c) for c in board_cards), position=position)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/solve_flop_turn_multiway", response_model=FlopSolveResponse)
async def solve_flop_turn_multiway_endpoint(
    board: str = Query(..., description="Exactly 3 cards, e.g. Jh7d2c"),
    pot: float = Query(10.0, gt=0, description="Pot entering the flop"),
    stack_bb: float = Query(40.0, gt=0, description="Effective stack behind, in big blinds"),
    iterations: int = Query(DEFAULT_FLOP_TURN_MULTIWAY_ITERATIONS, gt=0, le=cfg.MAX_FLOP_TURN_MULTIWAY_ITERATIONS),
    position: str | None = Query(None, description="OOP, MID, or IP — defaults to OOP, the first to act"),
):
    """Same 3-max multiway flop as /solve_flop_multiway, but chains a
    showdown-eligible flop terminal into a real multiway turn decision
    (M37, wiring up M36's solve_flop_turn_multiway) instead of averaging
    every remaining runout immediately."""
    try:
        board_cards = tuple(parse_cards(board))
        if len(board_cards) != 3:
            raise ValueError(f"board must have exactly 3 cards for a flop, got {len(board_cards)}")
        result = await run_in_threadpool(_get_or_solve_flop_turn_multiway, board_cards, pot, stack_bb, iterations)
        return format_flop_response(result, board="".join(str(c) for c in board_cards), position=position)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/solve_flop_to_river_multiway", response_model=FlopSolveResponse)
async def solve_flop_to_river_multiway_endpoint(
    board: str = Query(..., description="Exactly 3 cards, e.g. Jh7d2c"),
    pot: float = Query(10.0, gt=0, description="Pot entering the flop"),
    stack_bb: float = Query(40.0, gt=0, description="Effective stack behind, in big blinds"),
    iterations: int = Query(DEFAULT_FLOP_TO_RIVER_MULTIWAY_ITERATIONS, gt=0, le=cfg.MAX_FLOP_TO_RIVER_MULTIWAY_ITERATIONS),
    position: str | None = Query(None, description="OOP, MID, or IP — defaults to OOP, the first to act"),
):
    """Same 3-max multiway flop as /solve_flop_multiway, chained all the
    way to a real multiway river decision (M40, wiring up M39's solve_
    flop_to_river_multiway) — the deepest runout this endpoint family
    exposes for a multiway origin, mirroring /solve_flop_to_river's own
    role at the 2-position endpoints."""
    try:
        board_cards = tuple(parse_cards(board))
        if len(board_cards) != 3:
            raise ValueError(f"board must have exactly 3 cards for a flop, got {len(board_cards)}")
        result = await run_in_threadpool(_get_or_solve_flop_to_river_multiway, board_cards, pot, stack_bb, iterations)
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


@app.post(
    "/solve_flop_from_path",
    deprecated=True,  # M64: superseded by POST /advise (flop advice, heads-up)
    response_model=FlopPathQueryResponse,
)
async def solve_flop_from_path_endpoint(request: ActionPathRequest):
    try:
        if len(request.action_path) > cfg.MAX_PATH_LENGTH:
            raise ValueError(f"action_path is too long ({len(request.action_path)} > {cfg.MAX_PATH_LENGTH})")
        board_cards = tuple(parse_cards(request.board))
        if len(board_cards) != 3:
            raise ValueError(f"board must have exactly 3 cards for a flop, got {len(board_cards)}")
        iterations = request.iterations if request.iterations is not None else DEFAULT_ITERATIONS
        if not 0 < iterations <= cfg.MAX_ITERATIONS:
            raise ValueError(f"iterations must be between 1 and {cfg.MAX_ITERATIONS}, got {iterations}")
        return await run_in_threadpool(
            _query_flop_from_path, request.action_path, request.stack_bb, board_cards, iterations, request.players
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post(
    "/solve_flop_multiway_from_path",
    deprecated=True,  # M64: superseded by POST /advise (flop advice, 3+ live positions)
    response_model=FlopMultiwayPathQueryResponse,
)
async def solve_flop_multiway_from_path_endpoint(request: MultiwayFlopPathRequest):
    """The multiway analog of /solve_flop_from_path (M24), closing this
    project's own long-named-open gap: connecting derive_ranges_from_path's
    already-N-general output (proven at 3-max in M35's own pipeline test)
    into a live multiway postflop endpoint. Requires a genuine 3+-live-
    position terminal — a 2-survivor path 422s here with a message
    pointing at /solve_flop_from_path instead, the endpoint that already
    serves that case (via the exact, not MCCFR-approximate, 2-position
    solver)."""
    try:
        if len(request.action_path) > cfg.MAX_PATH_LENGTH:
            raise ValueError(f"action_path is too long ({len(request.action_path)} > {cfg.MAX_PATH_LENGTH})")
        board_cards = tuple(parse_cards(request.board))
        if len(board_cards) != 3:
            raise ValueError(f"board must have exactly 3 cards for a flop, got {len(board_cards)}")
        iterations = request.iterations if request.iterations is not None else DEFAULT_ITERATIONS
        if not 0 < iterations <= cfg.MAX_ITERATIONS:
            raise ValueError(f"iterations must be between 1 and {cfg.MAX_ITERATIONS}, got {iterations}")
        flop_iterations = (
            request.flop_iterations
            if request.flop_iterations is not None
            else cfg.DEFAULT_MULTIWAY_PATH_QUERY_FLOP_ITERATIONS
        )
        if not 0 < flop_iterations <= cfg.MAX_MULTIWAY_PATH_QUERY_FLOP_ITERATIONS:
            raise ValueError(
                f"flop_iterations must be between 1 and {cfg.MAX_MULTIWAY_PATH_QUERY_FLOP_ITERATIONS}, "
                f"got {flop_iterations}"
            )
        return await run_in_threadpool(
            _query_flop_multiway_from_path,
            request.action_path,
            request.stack_bb,
            board_cards,
            iterations,
            flop_iterations,
            request.players,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/preflop_walk", response_model=PreflopWalkResponse)
async def preflop_walk_endpoint(request: PreflopWalkRequest):
    try:
        if len(request.action_path) > cfg.MAX_PATH_LENGTH:
            raise ValueError(f"action_path is too long ({len(request.action_path)} > {cfg.MAX_PATH_LENGTH})")
        iterations = request.iterations if request.iterations is not None else DEFAULT_ITERATIONS
        if not 0 < iterations <= cfg.MAX_ITERATIONS:
            raise ValueError(f"iterations must be between 1 and {cfg.MAX_ITERATIONS}, got {iterations}")
        return await run_in_threadpool(
            _preflop_walk, request.stack_bb, request.action_path, iterations, request.players
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post(
    "/solve_turn_from_path",
    deprecated=True,  # M64: superseded by POST /advise (turn advice, heads-up)
    response_model=TurnPathQueryResponse,
)
async def solve_turn_from_path_endpoint(request: TurnPathRequest):
    try:
        if len(request.preflop_action_path) > cfg.MAX_PATH_LENGTH:
            raise ValueError(
                f"preflop_action_path is too long ({len(request.preflop_action_path)} > {cfg.MAX_PATH_LENGTH})"
            )
        if len(request.flop_action_path) > cfg.MAX_PATH_LENGTH:
            raise ValueError(f"flop_action_path is too long ({len(request.flop_action_path)} > {cfg.MAX_PATH_LENGTH})")
        board_cards = tuple(parse_cards(request.board))
        if len(board_cards) != 3:
            raise ValueError(f"board must have exactly 3 cards for a flop, got {len(board_cards)}")
        turn_cards = tuple(parse_cards(request.turn_card))
        if len(turn_cards) != 1:
            raise ValueError(f"turn_card must have exactly 1 card, got {len(turn_cards)}")
        iterations = request.iterations if request.iterations is not None else DEFAULT_ITERATIONS
        if not 0 < iterations <= cfg.MAX_ITERATIONS:
            raise ValueError(f"iterations must be between 1 and {cfg.MAX_ITERATIONS}, got {iterations}")
        turn_iterations = (
            request.turn_iterations if request.turn_iterations is not None else DEFAULT_FLOP_TURN_ITERATIONS
        )
        if not 0 < turn_iterations <= cfg.MAX_FLOP_TURN_ITERATIONS:
            raise ValueError(
                f"turn_iterations must be between 1 and {cfg.MAX_FLOP_TURN_ITERATIONS}, got {turn_iterations}"
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
            request.players,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/advise", response_model=AdviseResponse)
async def advise_endpoint(request: AdviseRequest):
    """M51: one front door for the whole real-situation advisor — your
    cards, the board, your position (implied by the action path), and
    what everyone did, in; GTO advice for the decision you actually
    face, out. Street depth is inferred from which fields are present
    (see AdviseRequest); each (street, table size) cell delegates to
    whichever sibling endpoint already serves it, so this adds a unified
    entry point without becoming a second implementation to keep in
    sync. `source` names which backend actually answered."""
    try:
        for field in ("preflop_action_path", "flop_action_path", "turn_action_path"):
            path = getattr(request, field)
            if path is not None and len(path) > cfg.MAX_PATH_LENGTH:
                raise ValueError(f"{field} is too long ({len(path)} > {cfg.MAX_PATH_LENGTH})")

        street = _infer_street(request)

        iterations = request.iterations if request.iterations is not None else DEFAULT_ITERATIONS
        if not 0 < iterations <= cfg.MAX_ITERATIONS:
            raise ValueError(f"iterations must be between 1 and {cfg.MAX_ITERATIONS}, got {iterations}")

        # Survivor count, NOT request.players — see _live_position_count
        # for the real bug this prevents (a full-ring hand folding down
        # to a heads-up flop must use the exact 2-position solver).
        multiway = street != "preflop" and _live_position_count(request, iterations) >= 3
        cell = (street, multiway)
        if cell in _ADVISE_UNSUPPORTED_CELLS:
            raise ValueError(_ADVISE_UNSUPPORTED_CELLS[cell])

        solve_iterations = None
        if street != "preflop":
            default_iters, max_iters = _ADVISE_ITERATION_CAPS[cell]
            solve_iterations = request.solve_iterations if request.solve_iterations is not None else default_iters
            if not 0 < solve_iterations <= max_iters:
                raise ValueError(
                    f"solve_iterations must be between 1 and {max_iters} for a {street} "
                    f"{'multiway' if multiway else 'heads-up'} query, got {solve_iterations}"
                )

        hero_combo = None
        if request.hero_cards is not None:
            hero_cards = tuple(parse_cards(request.hero_cards))
            if len(hero_cards) != 2:
                raise ValueError(f"hero_cards must have exactly 2 cards, got {len(hero_cards)}")
            hero_combo = HandCombo(hero_cards[0], hero_cards[1])

        raw = await run_in_threadpool(
            _advise, request, street, iterations, solve_iterations, hero_combo, multiway
        )

        hero = None
        if hero_combo is not None:
            # hero_key, not str(hero_combo) — preflop keys strategies by
            # hand CLASS, every postflop street by concrete combo, and
            # the cell that answered is the only thing that knows which.
            hero_key = raw.get("hero_key") or str(hero_combo)
            hero = {
                "cards": str(hero_combo),
                "in_range": bool(raw.get("hero_in_range", False)),
                "strategy": raw["strategy"].get(hero_key),
                "trained": None if raw.get("trained") is None else raw["trained"].get(hero_key),
                # Distinct from `trained` above, and easy to conflate:
                # `trained` is about the POSTFLOP solve node hero's
                # advice was read from; `range_trained` is about the
                # PREFLOP derivation that produced the range fed into
                # that solve. Either can be untrustworthy independently.
                "range_trained": raw.get("hero_range_trained"),
            }

        return {
            "street": raw["street"],
            "players": request.players,
            "positions": raw["positions"],
            "position": raw["position"],
            "player_to_act": raw.get("player_to_act"),
            "is_terminal": raw.get("is_terminal", False),
            "pot": raw["pot"],
            "effective_stack_bb": raw["effective_stack_bb"],
            # M101. Falls back to effective_stack_bb rather than raising
            # if some cell has not supplied it: a missing bound would be
            # a worse failure than a conservative one, and the fallback
            # is exactly right at a street's opening decision, which is
            # where the two agree.
            "max_affordable_bb": raw.get("max_affordable_bb", raw["effective_stack_bb"]),
            "strategy": raw["strategy"],
            "trained": raw.get("trained"),
            "hero": hero,
            "source": raw["source"],
            "solve_iterations": raw.get("solve_iterations"),
            "elapsed_seconds": raw["elapsed_seconds"],
            "range_confidence": raw.get("range_confidence"),
            # M76: whether this cell's SOLVER can be trusted at all,
            # which is a different question from whether a given hand was
            # trained. Keyed off the ORIGIN table size the client asked
            # about, not the number of players still live: 9-max's
            # problem is that its preflop solve divides iterations among
            # nine seats, and that has already happened by the time
            # anyone folds. See cfg.LOW_CONFIDENCE_TABLE_SIZES.
            "solver_confidence": _solver_confidence(raw, request.players, hero)[0],
            "solver_confidence_reason": _solver_confidence(raw, request.players, hero)[1],
            # M98: scoped to the sizing axis, and only preflop. A
            # multiway preflop solve answers "play or fold" reliably and
            # "which size" unreliably; reporting one number for both hid
            # the second. Keyed off the ORIGIN table size for the same
            # reason solver_confidence is — the preflop solve happened
            # before anyone folded. See cfg.SIZING_CAVEAT_TABLE_SIZES.
            "sizing_confidence": (
                "low"
                if raw.get("street") == "preflop"
                and request.players in cfg.SIZING_CAVEAT_TABLE_SIZES
                else "high"
            ),
            "sizing_confidence_reason": (
                cfg.SIZING_CAVEAT_REASON
                if raw.get("street") == "preflop"
                and request.players in cfg.SIZING_CAVEAT_TABLE_SIZES
                else None
            ),
            # M128: the postflop counterpart. Scoped to the AGGRESSION
            # axis — how often to bet or raise — because that is what was
            # measured to be unstable. Sweeping the cost-only range cap
            # from 10 to 26 classes moves a value hand's raising
            # frequency non-monotonically across a 250x range, and no
            # affordable setting is stable. The fold-versus-play call
            # held up across 275 advised decisions (M127), so it is
            # deliberately NOT implicated here.
            #
            # Every postflop street, every table size: the cap applies to
            # all of them. M99's reason for not flagging postflop was
            # that its terminal-pricing distortion was an order of
            # magnitude below preflop's; this is a different and much
            # larger effect, measured directly.
            "aggression_confidence": (
                "low" if raw.get("street") != "preflop" else "high"
            ),
            "aggression_confidence_reason": (
                _aggression_reason(raw, hero)
                if raw.get("street") != "preflop"
                else None
            ),
            # M166: the number behind the band note, so a caller can act on
            # it directly instead of parsing prose. None when hero's cards
            # or the board are missing.
            "hand_strength_percentile": (
                _hand_strength_percentile(raw, hero)
                if raw.get("street") != "preflop"
                else None
            ),
            # M144/F40: what the tree could actually offer here. Derived
            # from the response's own rows, not from config, so it stays
            # honest if the sizing constants change.
            "modelled_bet_sizes": _modelled_bet_sizes(raw),
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        # An unsupported (street, table size) cell — e.g. players=6 at a
        # street whose own cap table has no entry. A clear 422, not a 500.
        raise HTTPException(status_code=422, detail=f"unsupported street/table-size combination: {exc}") from exc


def _node_is_untrained(raw: dict) -> bool:
    """True when NOTHING at this decision was trained, so every row is the
    solver's uniform prior rather than a computed strategy.

    M145/F41. Deliberately the unambiguous case only. A single hand can
    read untrained for a benign reason - `trained_hands` documents that a
    hand with zero reach in this position's range is untrained at any
    iteration count - so flagging on one hand would fire constantly and
    make `low` meaningless. Zero trained hands at the whole node cannot
    be benign: it means the node was never visited.
    """
    trained = raw.get("trained")
    if not isinstance(trained, dict) or not trained:
        return False
    return not any(trained.values())


def _hero_row_is_the_prior(hero: dict | None) -> bool:
    """True when HERO's own row is exactly the uniform prior.

    M149/F43. Distinct from `_node_is_untrained`, which asks whether the
    whole node went unvisited. A hand can be visited many times and still
    average to exactly the prior: `current_strategy()` returns it
    whenever every regret is <= 0, which M73 measured at ~70% of rows. So
    `trained` (i.e. "was visited") can be true while the answer is the
    starting assumption — measured at 6-max with AA facing a 4-bet, where
    the response claimed `hero.trained: true` and `solver_confidence:
    "high"` beside fold/call/jam at 0.3333 each.

    EXACT uniformity only. A near-uniform row is a real computed answer
    that happens to sit near indifference; flagging those would make
    "low" the normal case and mean nothing.
    """
    # Takes the hero block DIRECTLY, not the raw solve dict: `hero` is
    # assembled in `advise` itself and never lands in `raw`, so a first
    # version reading `raw.get("hero")` silently never fired.
    if not isinstance(hero, dict):
        return False
    # M163/F47: this used to return False whenever `trained` was not
    # True, on the reasoning that an untrained hero already carries a
    # louder warning. It does carry one - in the `hero.trained` FIELD -
    # but `solver_confidence` never read that field, so the headline
    # signal still said "high" over a row that is purely the prior.
    # Measured in a 120-hand session: a six-handed flop decision returned
    # 0.3333 across fold/call/all-in and called itself high confidence.
    #
    # Structural now: this asks only whether the row IS the prior.
    # `_solver_confidence` picks which reason to give, because "reached
    # but never preferred" and "never reached" are different news.
    row = hero.get("strategy") or {}
    if len(row) < 2:
        return False
    values = list(row.values())
    return max(values) - min(values) < 1e-9


def _solver_confidence(raw: dict, players: int, hero: dict | None = None):
    """(level, reason) for the headline confidence signal.

    M145/F41: this used to depend only on TABLE SIZE, so a node whose
    every row was the uniform prior still reported "high". Both causes
    now feed it, and both reasons are reported when both apply — they are
    different problems and a user acting on one should still see the
    other.
    """
    reasons = []
    table_size_reason = cfg.LOW_CONFIDENCE_TABLE_SIZES.get(players)
    if table_size_reason:
        reasons.append(table_size_reason)
    if _node_is_untrained(raw):
        reasons.append(cfg.UNTRAINED_NODE_REASON)
    elif _hero_row_is_the_prior(hero):
        # `elif`: when the whole node is untrained the node-level reason
        # already says so, and both would be the same news twice.
        #
        # M163/F47: which reason depends on whether the hand was reached
        # at all. Both are the prior; only one of them was ever looked at.
        reasons.append(
            cfg.UNIFORM_ROW_REASON
            if isinstance(hero, dict) and hero.get("trained") is True
            else cfg.UNTRAINED_HERO_ROW_REASON
        )
    if not reasons:
        return "high", None
    return "low", " ".join(reasons)


def _modelled_bet_sizes(raw: dict) -> list:
    """The bet sizes this node's tree actually offered, ascending.

    M144/F40. `FLOP_TO_RIVER_RAISE_SIZES` is empty at production
    settings, so a river node's only actions are check/call and all-in —
    a player asking how much to bet the river cannot be answered at all.
    Nothing in the response said so, which made `all_in: 0.11` read as
    "shoving beat betting smaller" when smaller was never legal.

    Read off the strategy rows rather than the config constants: the
    point is to report what THIS node could express, and deriving it
    from the answer keeps it true if the constants move.
    """
    rows = list((raw.get("strategy") or {}).values())
    hero = raw.get("hero") or {}
    if isinstance(hero, dict) and hero.get("strategy"):
        rows.append(hero["strategy"])
    sizes = set()
    for row in rows:
        for action in row:
            if ":" in action:
                try:
                    sizes.add(float(action.split(":", 1)[1]))
                except ValueError:
                    continue
    return sorted(sizes)


def _is_facing_a_bet(raw: dict) -> bool:
    """True when hero can fold here — i.e. is facing a bet.

    M185. Derived from the response's own rows, not from the request's
    action path, for the reason M144 built `_has_no_intermediate_bet_size`
    the same way: the rows are what the tree actually offered, so this
    stays true if the path shapes or the size menus change. Folding is
    only ever legal facing a bet — checking is free otherwise — so its
    presence IS the signal.
    """
    rows = list((raw.get("strategy") or {}).values())
    hero = raw.get("hero") or {}
    if isinstance(hero, dict) and hero.get("strategy"):
        rows.append(hero["strategy"])
    return any(action == "fold" or action.startswith("fold")
               for row in rows for action in row)


def _has_no_intermediate_bet_size(raw: dict) -> bool:
    """True when the only way to put money in here is all-in."""
    rows = list((raw.get("strategy") or {}).values())
    hero = raw.get("hero") or {}
    if isinstance(hero, dict) and hero.get("strategy"):
        rows.append(hero["strategy"])
    if not rows:
        return False
    saw_all_in = False
    for row in rows:
        for action in row:
            if action.startswith("raise:"):
                return False
            if action.startswith("all_in"):
                saw_all_in = True
    return saw_all_in


def _hand_strength_percentile(raw: dict, hero: dict | None) -> float | None:
    """How strong hero's hand is on this board, 0.0-1.0, or None.

    M166. Returns None rather than guessing whenever the inputs are not
    both present and parseable - a wrong strength reading would attach
    the wrong reliability note, which is worse than attaching none.
    """
    if not isinstance(hero, dict):
        return None
    cards = hero.get("cards")
    board = raw.get("board")
    if not cards or not board:
        return None
    try:
        hero_cards = parse_cards(cards)
        board_cards = parse_cards(board)
        if len(hero_cards) != 2 or not 3 <= len(board_cards) <= 5:
            return None
        return strength_percentile(HandCombo(*hero_cards), tuple(board_cards))
    except (ValueError, KeyError, TypeError):
        return None


def _aggression_reason(raw: dict, hero: dict | None = None) -> str:
    """The postflop caveat, calibrated to hero's own hand where possible.

    M166: the blanket caveat told every player the same thing, while the
    measured error splits sharply by hand strength - nothing in the upper
    two bands exceeded 0.10 and half the weak band did. Leading with which
    band this hand is in turns a warning a player learns to ignore into
    one that distinguishes the answers worth acting on.

    The band note goes FIRST because it is the part that changes between
    requests; the standing caveat that follows does not.
    """
    reason = cfg.POSTFLOP_AGGRESSION_CAVEAT_REASON
    percentile = _hand_strength_percentile(raw, hero)
    # Certification is FLOP-ONLY. M167 measured the threshold on flop spots
    # and applied it to every street; M168 checked the turn and refused it
    # there. M175 re-checked the turn against the M173 standalone solve,
    # 24 spots against 8, and it is STILL refused — but for a corrected
    # reason. M168 found an inversion (the certified band worse, 3 of 4
    # over 0.10); at 12 spots per band the two bands are indistinguishable
    # (certified mean 0.0954, weak 0.1032, correlation +0.057) and 4 of 12
    # certified-band spots still exceed 0.10, worst 0.3038 at percentile
    # 0.884. So strength carries no signal on the turn rather than an
    # inverted one. Certifying needs positive evidence for the street;
    # there is none here, in either direction.
    street = raw.get("street")
    if street not in cfg.CERTIFY_RELIABILITY_ON_STREETS:
        # M177: the river is no longer UNMEASURED — it was measured over 56
        # spots and certification was refused, with error concentrated in
        # the band a certificate would vouch for. Telling a river player
        # "accuracy here has not been measured" is now false, and it buries
        # the one thing that IS known and actionable: strong hands are the
        # unreliable ones there, and they err toward committing chips.
        # M180: three streets, three evidential positions, three notes.
        # The flop is MEASURED and refused (so the "not measured the way
        # the flop has" wording is self-contradictory there); the river is
        # measured with a one-sided strength signal; the turn is measured
        # with no usable signal at all.
        reason = ({"flop": cfg.FLOP_MEASURED_NOTE,
                   "river": cfg.RIVER_MEASURED_NOTE}.get(
                      street, cfg.UNMEASURED_STREET_NOTE)) + reason
    elif percentile is not None:
        # M167: certify where reliability was MEASURED, and say "not known"
        # elsewhere. M166 had this the other way round - it asserted that
        # weak hands specifically were unreliable, and pooling 44 spots
        # gave a strength/error correlation of -0.130. The one thing that
        # held was the top of the range being clean.
        if percentile >= cfg.RELIABLE_HAND_STRENGTH_PERCENTILE:
            reason = cfg.RELIABLE_HAND_NOTE + reason
        else:
            reason = cfg.UNCERTAIN_HAND_NOTE + reason
    if _has_no_intermediate_bet_size(raw):
        reason += cfg.BET_SIZING_COVERAGE_NOTE
    # M185: where the measured cost actually is. Appended LAST because it
    # is the part a player can act on immediately — the notes before it
    # describe what is known about the street, this describes what is
    # known about this decision.
    if _is_facing_a_bet(raw):
        reason += cfg.FACING_A_BET_COST_NOTE
    return reason


@app.post(
    "/solve_river_from_path",
    deprecated=True,  # M64: superseded by POST /advise (river advice, heads-up)
    response_model=RiverPathQueryResponse,
)
async def solve_river_from_path_endpoint(request: RiverPathRequest):
    """Real river-level advice (M46) — one street further than
    /solve_turn_from_path, closing the last street this project's
    real-action-path thread had left uncovered. A real preflop path, a
    real flop board/action path, a real dealt turn card, a real turn
    action path (new — the turn is itself a full betting round), and a
    real dealt river card, in; the river decision's real strategy, out.
    See the module docstring for the measured cost this required capping
    ranges by combo count, not class, to keep tolerable."""
    try:
        if len(request.preflop_action_path) > cfg.MAX_PATH_LENGTH:
            raise ValueError(
                f"preflop_action_path is too long ({len(request.preflop_action_path)} > {cfg.MAX_PATH_LENGTH})"
            )
        if len(request.flop_action_path) > cfg.MAX_PATH_LENGTH:
            raise ValueError(f"flop_action_path is too long ({len(request.flop_action_path)} > {cfg.MAX_PATH_LENGTH})")
        if len(request.turn_action_path) > cfg.MAX_PATH_LENGTH:
            raise ValueError(f"turn_action_path is too long ({len(request.turn_action_path)} > {cfg.MAX_PATH_LENGTH})")
        board_cards = tuple(parse_cards(request.board))
        if len(board_cards) != 3:
            raise ValueError(f"board must have exactly 3 cards for a flop, got {len(board_cards)}")
        turn_cards = tuple(parse_cards(request.turn_card))
        if len(turn_cards) != 1:
            raise ValueError(f"turn_card must have exactly 1 card, got {len(turn_cards)}")
        river_cards = tuple(parse_cards(request.river_card))
        if len(river_cards) != 1:
            raise ValueError(f"river_card must have exactly 1 card, got {len(river_cards)}")
        iterations = request.iterations if request.iterations is not None else DEFAULT_ITERATIONS
        if not 0 < iterations <= cfg.MAX_ITERATIONS:
            raise ValueError(f"iterations must be between 1 and {cfg.MAX_ITERATIONS}, got {iterations}")
        river_iterations = (
            request.river_iterations if request.river_iterations is not None else cfg.DEFAULT_RIVER_PATH_QUERY_ITERATIONS
        )
        if not 0 < river_iterations <= cfg.MAX_RIVER_PATH_QUERY_ITERATIONS:
            raise ValueError(
                f"river_iterations must be between 1 and {cfg.MAX_RIVER_PATH_QUERY_ITERATIONS}, got {river_iterations}"
            )
        return await run_in_threadpool(
            _query_river_from_path,
            request.preflop_action_path,
            request.flop_action_path,
            turn_cards[0],
            request.turn_action_path,
            river_cards[0],
            request.stack_bb,
            board_cards,
            iterations,
            river_iterations,
            request.players,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post(
    "/solve_turn_multiway_from_path",
    deprecated=True,  # M64: superseded by POST /advise (turn advice, 3+ live positions)
    response_model=TurnMultiwayPathQueryResponse,
)
async def solve_turn_multiway_from_path_endpoint(request: MultiwayTurnPathRequest):
    """The multiway analog of /solve_turn_from_path (M26), closing M42/
    M43's own remaining "turn-depth" open item — for a real preflop path
    that leaves 3+ live positions at the flop (a case /solve_turn_from_
    path structurally can't serve, mirroring /solve_flop_multiway_from_
    path's own M42 scope boundary relative to /solve_flop_from_path)."""
    try:
        if len(request.preflop_action_path) > cfg.MAX_PATH_LENGTH:
            raise ValueError(
                f"preflop_action_path is too long ({len(request.preflop_action_path)} > {cfg.MAX_PATH_LENGTH})"
            )
        if len(request.flop_action_path) > cfg.MAX_PATH_LENGTH:
            raise ValueError(f"flop_action_path is too long ({len(request.flop_action_path)} > {cfg.MAX_PATH_LENGTH})")
        board_cards = tuple(parse_cards(request.board))
        if len(board_cards) != 3:
            raise ValueError(f"board must have exactly 3 cards for a flop, got {len(board_cards)}")
        turn_cards = tuple(parse_cards(request.turn_card))
        if len(turn_cards) != 1:
            raise ValueError(f"turn_card must have exactly 1 card, got {len(turn_cards)}")
        iterations = request.iterations if request.iterations is not None else DEFAULT_ITERATIONS
        if not 0 < iterations <= cfg.MAX_ITERATIONS:
            raise ValueError(f"iterations must be between 1 and {cfg.MAX_ITERATIONS}, got {iterations}")
        flop_iterations = (
            request.flop_iterations
            if request.flop_iterations is not None
            else cfg.DEFAULT_MULTIWAY_TURN_PATH_QUERY_FLOP_ITERATIONS
        )
        if not 0 < flop_iterations <= cfg.MAX_MULTIWAY_TURN_PATH_QUERY_FLOP_ITERATIONS:
            raise ValueError(
                f"flop_iterations must be between 1 and {cfg.MAX_MULTIWAY_TURN_PATH_QUERY_FLOP_ITERATIONS}, "
                f"got {flop_iterations}"
            )
        return await run_in_threadpool(
            _query_turn_multiway_from_path,
            request.preflop_action_path,
            request.flop_action_path,
            turn_cards[0],
            request.stack_bb,
            board_cards,
            iterations,
            flop_iterations,
            request.players,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# Registered last so it only catches requests /solve doesn't match —
# Starlette checks routes in registration order, and a Mount only
# matches as a fallback for paths its earlier siblings didn't claim.
# html=True serves frontend/dist/index.html for "/" and other paths
# (client-side routing would need this too, though this app has none).
if cfg.FRONTEND_DIST_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(cfg.FRONTEND_DIST_DIR), html=True), name="frontend")
