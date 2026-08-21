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
DEMO_MULTIWAY_FLOP_CLASSES pool (one suited class per position, 11
combos total after board-legal expansion). Deliberately 3-max only —
see DEMO_MULTIWAY_FLOP_CLASSES' own comment for why 6-max/9-max
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
space, so MAX_FLOP_TURN_MULTIWAY_ITERATIONS is set far more
conservatively than MAX_FLOP_MULTIWAY_ITERATIONS's own generous 10x-
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

Measured live, at the same 11-combo DEMO_MULTIWAY_FLOP_CLASSES pool:
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
enumeration. MAX_FLOP_TO_RIVER_MULTIWAY_ITERATIONS is therefore set
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
restricted to MULTIWAY_TABLE_CONFIGS' own small DEMO_MULTIWAY_HANDS
pool (8 real classes), not the full 169-class pool /solve_flop_from_
path solves over at players=2 — so this endpoint's own class cap
(MAX_MULTIWAY_PATH_QUERY_CLASSES_PER_POSITION) only ever ranks among
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
flat, unlike DEMO_MULTIWAY_FLOP_CLASSES' own tiny 11-combo pool: 200
iters ~22.46s, 500 iters ~36.76s, 1000 iters ~48.20s, 2000 iters
~58.13s. MAX_MULTIWAY_PATH_QUERY_FLOP_ITERATIONS is therefore set to
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

MAX_MULTIWAY_TURN_PATH_QUERY_CLASSES_PER_POSITION ended up landing on
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
already too coarse — a single class can expand to up to 12 combos, and
capping to just one class per side (up to 16 combos total) measured
224.43s at this function's own already-tight default iteration count
(20). Capping by raw COMBO count instead, at that same iteration count:
1 combo/side (2 total) -> 14.10s; 2/side (4 total) -> 27.94s; 3/side (6
total) -> 43.00s — a real, roughly linear ~7s/combo relationship.
RIVER_PATH_QUERY_MAX_COMBOS_PER_SIDE is set to 3 (~43s), landing in the
same "slow but tolerable for a live request" bracket /solve_flop_to_
river's own fixed-demo endpoint was accepted in at M14 (~63-105s).
river_iterations' own cap mirrors MAX_FLOP_TO_RIVER_ITERATIONS' own
"==default, zero headroom" discipline, for the identical reason: cost
at this scale is already at the outer edge of tolerable at the default
alone.

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
    ensure_flop_turn_multiway_branch,
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
# Fully resolving 6-max's convergence is real, separate future work.
MULTIWAY_TABLE_CONFIGS = {
    3: {"positions": ("BTN", "SB", "BB"), "iterations": 100_000},
    6: {"positions": ("UTG", "MP", "CO", "BTN", "SB", "BB"), "iterations": 300},
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

# /solve_river_from_path's (M46) own cost controls — capped by COMBO
# count directly, not by class the way every other path-derived
# endpoint's own cap is (MAX_PATH_QUERY_CLASSES_PER_SIDE, MAX_TURN_
# PATH_QUERY_CLASSES_PER_SIDE, MAX_MULTIWAY_PATH_QUERY_CLASSES_PER_
# POSITION). Real, measured reason: solve_flop_to_river's cost scales so
# steeply with combo-pool size that even a single CLASS (which can
# expand to up to 12 combos, e.g. an offsuit hand) is already too coarse
# a lever — measured directly, same real preflop line/board/iterations
# throughout: capping to the top class per side (up to 16 combos after
# expansion) cost 224.43s at iterations=20, the solver's own already-
# tight default. Capping by raw combo count instead: 1 combo/side (2
# total) -> 14.10s; 2/side (4 total) -> 27.94s; 3/side (6 total) ->
# 43.00s — a real, roughly linear ~7s/combo relationship, not the
# unpredictable jump a class-sized cap produces. Set to 3 combos per
# side (~43s), landing in the same "slow but tolerable for a live
# request" bracket /solve_flop_to_river's own fixed-demo endpoint was
# already accepted in at M14 (~63-105s), while still giving a caller
# more than one real combo of range diversity per side.
RIVER_PATH_QUERY_MAX_COMBOS_PER_SIDE = 3

# solve_flop_to_river's own cost is dominated by combo-pool size, not
# iteration count, at these tiny scales (see the measurement above — all
# three combo-count points were measured at the SAME iterations=20).
# Mirrors MAX_FLOP_TO_RIVER_ITERATIONS' own "==default, zero headroom"
# discipline for the identical reason: this function's cost is already
# at the outer edge of tolerable for a live request at its own default,
# so `river_iterations` can only ever request a faster, noisier result,
# never a slower one.
DEFAULT_RIVER_PATH_QUERY_ITERATIONS = DEFAULT_FLOP_TO_RIVER_ITERATIONS
MAX_RIVER_PATH_QUERY_ITERATIONS = DEFAULT_FLOP_TO_RIVER_ITERATIONS

# /solve_flop_multiway_from_path's (M42) own cost controls — the
# multiway analog of MAX_PATH_QUERY_CLASSES_PER_SIDE/PATH_QUERY_
# ITERATIONS, separately measured since solve_flop_multiway's own cost
# curve is far steeper than solve_flop's (M35's own finding: pool size
# is the dominant cost driver, compounded by MCCFR's opponent-sampling
# cache-miss rate). Unlike /solve_flop_from_path's preflop leg (which
# solves over the FULL 169-class pool at players=2), this endpoint's
# preflop leg is already restricted to MULTIWAY_TABLE_CONFIGS' own
# small DEMO_MULTIWAY_HANDS pool (8 real classes) whenever players != 2
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
MAX_MULTIWAY_PATH_QUERY_CLASSES_PER_POSITION = 2

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
MAX_MULTIWAY_TURN_PATH_QUERY_CLASSES_PER_POSITION = 2
DEFAULT_MULTIWAY_TURN_PATH_QUERY_FLOP_ITERATIONS = DEFAULT_FLOP_TURN_MULTIWAY_ITERATIONS
MAX_MULTIWAY_TURN_PATH_QUERY_FLOP_ITERATIONS = 200

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
# /solve_flop_multiway's and /solve_flop_turn_multiway's own dicts (M37)
# — same "each endpoint gets its own" reasoning as the pair above; a
# shared dict would let an identical (board, pot, stack_bb, iterations)
# key collide between the two endpoints despite their different
# max_raises/chance-dispatch behavior.
_flop_multiway_cache: dict = {}
_flop_multiway_lock = threading.Lock()
_flop_turn_multiway_cache: dict = {}
_flop_turn_multiway_lock = threading.Lock()
# /solve_flop_to_river_multiway's own dict (M40) — same "each endpoint
# gets its own" reasoning as every dict above.
_flop_to_river_multiway_cache: dict = {}
_flop_to_river_multiway_lock = threading.Lock()
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

# /solve_flop_multiway_from_path's (M42) own plain dict cache —
# deliberately not a partitioned "one library dict per situation" the
# way _path_query_libraries is for /solve_flop_from_path: this endpoint
# doesn't go through query_strategy/query_strategy_from_path at all (both
# are 2-position machinery — solve_flop_multiway is called directly
# instead), so there's no canonical-library collision risk to partition
# against. Keyed on everything the derived situation and the solve
# actually depend on: the action path, players, stack_bb, board, the
# preflop-leg iterations, and flop_iterations — two different requests
# that happen to derive an identical range/pot/stack still get correctly
# separate cache entries if either iteration count differs.
_flop_multiway_path_cache: dict = {}
_flop_multiway_path_lock = threading.Lock()

# /solve_turn_multiway_from_path's (M44) own plain dict cache — same
# "no canonical library, keyed on everything the solve depends on"
# reasoning as _flop_multiway_path_cache above. Keyed only on the
# PREFLOP action path (not flop_action_path/turn_card, resolved by
# walking the already-solved tree afterward — the same "resolving is
# free, re-solving isn't" reasoning _turn_path_cache's own M26 key
# already established), plus players/stack_bb/board/the preflop-leg
# iterations/flop_iterations. The lock also guards ensure_flop_turn_
# multiway_branch's own on-demand-build-and-cache call (see
# _query_turn_multiway_from_path below) — that call mutates a cached
# StrategyResult's own chance_data dict in place, so it needs the same
# protection the cache dict's own reads/writes already get.
_turn_multiway_path_cache: dict = {}
_turn_multiway_path_lock = threading.Lock()

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

# M46's own plain-dict cache for solve_flop_to_river results — same
# shape/reasoning as _turn_path_cache above (keyed on what the solve
# itself depends on: preflop_action_path, stack_bb, its own iterations,
# board, river_iterations; deliberately NOT flop_action_path/turn_card/
# turn_action_path/river_card, resolved by walking the already-solved
# tree afterward instead of re-solving).
_river_path_cache: dict = {}
_river_path_lock = threading.Lock()


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


def _get_or_solve_flop_multiway(board_cards: tuple, pot: float, stack_bb: float, iterations: int) -> StrategyResult:
    """Solves (or returns the cached result of solving) DEMO_MULTIWAY_
    FLOP_CLASSES' board-legal expansion via solve_flop_multiway (M35) —
    same shape as _get_or_solve_flop/_get_or_solve_flop_turn, own cache
    dict (see the module-level comment by _flop_multiway_cache for why a
    shared one would be unsafe). Unlike those two-position helpers,
    DEMO_MULTIWAY_FLOP_CLASSES is itself a {position: {StartingHand:
    weight}} dict (one entry per DEMO_MULTIWAY_FLOP_POSITIONS), not two
    separate hero_/villain_range parameters — expanded per position here
    via the same range_from_class_frequencies call the two-position
    helpers already use, just looped."""
    key = (board_cards, round(pot, 2), round(stack_bb), iterations)
    with _flop_multiway_lock:
        cached = _flop_multiway_cache.get(key)
    if cached is not None:
        return cached

    exclude = frozenset(board_cards)
    position_ranges = {
        position: range_from_class_frequencies(classes, exclude=exclude)
        for position, classes in DEMO_MULTIWAY_FLOP_CLASSES.items()
    }
    if any(not r for r in position_ranges.values()):
        raise ValueError(f"board {''.join(str(c) for c in board_cards)!r} blocks every demo-range combo for at least one position")

    result = solve_flop_multiway(
        board=board_cards,
        position_ranges=position_ranges,
        pot=pot,
        effective_stack_bb=stack_bb,
        positions=DEMO_MULTIWAY_FLOP_POSITIONS,
        raise_sizes=MULTIWAY_FLOP_RAISE_SIZES,
        max_raises=MULTIWAY_FLOP_MAX_RAISES,
        iterations=iterations,
    )

    with _flop_multiway_lock:
        _flop_multiway_cache[key] = result
    return result


def _get_or_solve_flop_turn_multiway(board_cards: tuple, pot: float, stack_bb: float, iterations: int) -> StrategyResult:
    """Same idea as _get_or_solve_flop_multiway, via solve_flop_turn_
    multiway (M36) and its own (more conservative — see MAX_FLOP_TURN_
    MULTIWAY_ITERATIONS) cache."""
    key = (board_cards, round(pot, 2), round(stack_bb), iterations)
    with _flop_turn_multiway_lock:
        cached = _flop_turn_multiway_cache.get(key)
    if cached is not None:
        return cached

    exclude = frozenset(board_cards)
    position_ranges = {
        position: range_from_class_frequencies(classes, exclude=exclude)
        for position, classes in DEMO_MULTIWAY_FLOP_CLASSES.items()
    }
    if any(not r for r in position_ranges.values()):
        raise ValueError(f"board {''.join(str(c) for c in board_cards)!r} blocks every demo-range combo for at least one position")

    result = solve_flop_turn_multiway(
        board=board_cards,
        position_ranges=position_ranges,
        pot=pot,
        effective_stack_bb=stack_bb,
        positions=DEMO_MULTIWAY_FLOP_POSITIONS,
        raise_sizes=MULTIWAY_FLOP_RAISE_SIZES,
        max_raises=MULTIWAY_FLOP_MAX_RAISES,
        iterations=iterations,
    )

    with _flop_turn_multiway_lock:
        _flop_turn_multiway_cache[key] = result
    return result


def _get_or_solve_flop_to_river_multiway(board_cards: tuple, pot: float, stack_bb: float, iterations: int) -> StrategyResult:
    """Same idea as _get_or_solve_flop_turn_multiway, via solve_flop_to_
    river_multiway (M39) and its own cache — see MAX_FLOP_TO_RIVER_
    MULTIWAY_ITERATIONS' own comment for why this endpoint's cap matches
    solve_flop_turn_multiway's rather than solve_flop_to_river's tiny
    2-position ones."""
    key = (board_cards, round(pot, 2), round(stack_bb), iterations)
    with _flop_to_river_multiway_lock:
        cached = _flop_to_river_multiway_cache.get(key)
    if cached is not None:
        return cached

    exclude = frozenset(board_cards)
    position_ranges = {
        position: range_from_class_frequencies(classes, exclude=exclude)
        for position, classes in DEMO_MULTIWAY_FLOP_CLASSES.items()
    }
    if any(not r for r in position_ranges.values()):
        raise ValueError(f"board {''.join(str(c) for c in board_cards)!r} blocks every demo-range combo for at least one position")

    result = solve_flop_to_river_multiway(
        board=board_cards,
        position_ranges=position_ranges,
        pot=pot,
        effective_stack_bb=stack_bb,
        positions=DEMO_MULTIWAY_FLOP_POSITIONS,
        raise_sizes=MULTIWAY_FLOP_RAISE_SIZES,
        max_raises=MULTIWAY_FLOP_MAX_RAISES,
        iterations=iterations,
    )

    with _flop_to_river_multiway_lock:
        _flop_to_river_multiway_cache[key] = result
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


def _get_or_solve_preflop_raw(stack_bb: float, iterations: int, players: int = 2) -> StrategyResult:
    """Solves (or returns the cached result of solving) a real preflop
    spot, caching the RAW StrategyResult — unlike _get_or_solve above,
    which formats the result and discards it. M24 needs the real
    tree/node_data to walk with derive_ranges_from_path, the same
    reason _get_or_solve_multiway already caches its own raw result
    rather than a formatted one. Its own cache dict, not _cache — a
    formatted dict and a raw StrategyResult are different
    representations, never mixed in one dict (mirrors every other
    cache-dict boundary in this file).

    `players` (M29): 2 (the original, still-default behavior) solves
    heads-up with the CALLER's own `iterations` — unchanged. `players`
    in MULTIWAY_TABLE_CONFIGS instead delegates outright to
    _get_or_solve_multiway, ignoring `iterations` entirely — the same
    "fixed menu" discipline M9's own MULTIWAY_TABLE_CONFIGS budgets
    exist to enforce (a client-controllable iteration count at
    multiway scale reopens exactly the cost/safety question those
    budgets were tuned to close), and it reuses THE SAME cached
    StrategyResult `GET /solve/{stack_bb}?players=N` already solves and
    caches — a user who's already loaded that table size's range chart
    triggers no redundant second solve when they open the wizard next.
    """
    if players != 2:
        if players not in MULTIWAY_TABLE_CONFIGS:
            valid = ", ".join(str(p) for p in [2, *MULTIWAY_TABLE_CONFIGS])
            raise ValueError(f"players must be one of {valid}")
        return _get_or_solve_multiway(stack_bb, players)

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


def _preflop_walk(stack_bb: float, action_kinds: list, iterations: int, players: int = 2) -> dict:
    """Orchestrates POST /preflop_walk: a real (cached, raw) preflop
    solve -> resolve the client's bare action kinds into real Actions,
    walking to the resulting node -> report what's legal from there.

    No range derivation, no board, no query_strategy — this is a pure
    tree-state query, so none of _query_flop_from_path's range-capping
    or partitioned-library machinery applies here.

    `players` (M29): defaults to 2 (heads-up, unchanged); any other
    supported table size walks that size's own real tree instead — see
    _get_or_solve_preflop_raw's own docstring for what that changes
    (a fixed iteration budget, not the caller's `iterations`).
    """
    preflop_result = _get_or_solve_preflop_raw(stack_bb, iterations, players=players)
    _actions, node = _resolve_action_path(preflop_result.root, action_kinds)
    live_positions = [p for p in preflop_result.config.positions if p not in node.folded]

    if isinstance(node, TerminalNode):
        return {
            "stack_bb": stack_bb,
            "action_path": list(action_kinds),
            "is_terminal": True,
            "player_to_act": None,
            "live_positions": live_positions,
            "positions": list(preflop_result.config.positions),
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
        "positions": list(preflop_result.config.positions),
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


def _cap_range_to_combos(class_frequencies: dict, max_combos: int, exclude: frozenset) -> dict:
    """M46's own combo-level analog of _cap_range — expands a {Starting
    Hand: weight} dict to real board-legal combos first (via combos.
    range_from_class_frequencies), THEN caps to the top `max_combos`
    combos by weight, rather than capping classes before expansion. See
    RIVER_PATH_QUERY_MAX_COMBOS_PER_SIDE's own comment for why: a
    class-level cap is too coarse a lever for solve_flop_to_river's own
    cost curve (a single class can expand to up to 12 combos)."""
    expanded = range_from_class_frequencies(class_frequencies, exclude=exclude)
    if len(expanded) <= max_combos:
        return expanded
    top_items = sorted(expanded.items(), key=lambda item: item[1], reverse=True)[:max_combos]
    return dict(top_items)


def _query_flop_from_path(
    action_kinds: list, stack_bb: float, board_cards: tuple, iterations: int, players: int = 2
) -> dict:
    """Orchestrates POST /solve_flop_from_path end to end: a real
    (cached, raw) preflop solve -> resolve the client's bare action
    kinds into real Actions -> derive_ranges_from_path (M16) -> cap
    both sides to MAX_PATH_QUERY_CLASSES_PER_SIDE (Finding 1) -> a
    private, per-(action_path, stack_bb, iterations, players) library
    (Finding 2, not the shared _flop_query_library _query_flop above
    uses) -> query_strategy_from_path (M23).

    `players` (M29): part of the partition key, not just a solve
    parameter — two DIFFERENT origin table sizes can legitimately share
    the exact same literal action-kind path (e.g. ["raise",
    "call_or_check"] is valid at both heads-up and 6-max), so omitting
    it from the key would let one silently serve the other's cached
    answer. DEMO_MULTIWAY_HANDS' own 8-class pool is already far
    smaller than MAX_PATH_QUERY_CLASSES_PER_SIDE would even cap to, so
    Finding 1's own uncapped-169-class-pool cost blowup doesn't recur
    here — multiway's curated pool was already the safe side of that
    finding before this milestone existed.

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
    preflop_result = _get_or_solve_preflop_raw(stack_bb, iterations, players=players)
    actions, _node = _resolve_action_path(preflop_result.root, action_kinds)
    path_scenario = derive_ranges_from_path(preflop_result, actions)

    # Known, deliberate gap (M29): path_scenario.trained (whether each
    # derived-range hand was genuinely backed by real solving along the
    # path, not the untrained default — see PathScenario's own
    # docstring) isn't surfaced in this endpoint's response. Real and
    # measured to matter at 6/9-max specifically (a deep 3-bet line's
    # derived range came back exactly uniform in testing), but exposing
    # it well needs its own response-shape decision (per-hand, like
    # `trained` below, or a per-position summary) — deferred rather than
    # bolted on here, the same "prove the core capability, name what's
    # deferred" discipline this project's own bridge milestones already
    # follow throughout (see CLAUDE.md's M29 entry).
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

    # M29: postflop_action_order, not a raw positions[0]/[1] unpack —
    # correct at any origin table size, not just heads-up (see its own
    # docstring for the real poker rule this replaces a heads-up-only
    # guess with).
    oop_position, ip_position = postflop_action_order(preflop_result.config.positions, path_scenario.live_positions)
    partition_key = (tuple(action_kinds), round(stack_bb), iterations, players)
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
        "players": players,
    }


def _query_flop_multiway_from_path(
    action_kinds: list, stack_bb: float, board_cards: tuple, iterations: int, flop_iterations: int, players: int = 3
) -> dict:
    """Orchestrates POST /solve_flop_multiway_from_path end to end: a
    real (cached, raw) preflop solve -> resolve the client's bare action
    kinds -> derive_ranges_from_path (M16, already N-position-general)
    -> require a genuine 3+-live-position terminal (a 2-survivor path
    stays /solve_flop_from_path's own job) -> cap every position's range
    to MAX_MULTIWAY_PATH_QUERY_CLASSES_PER_POSITION -> postflop_action_
    order (M29, already N-general per its own docstring) for the correct
    real acting order -> solve_flop_multiway (M35) directly, behind a
    plain per-(action_path, players, stack_bb, board, iterations,
    flop_iterations) cache — not query_strategy_from_path, which is
    2-position machinery all the way down (query_strategy -> solve_flop
    -> build_board_equity_table, none of which accept a 3+-position
    range dict).

    `players` (M42, following M29's own precedent): part of the cache
    key, for the identical collision reason /solve_flop_from_path's
    partition key and /solve_turn_from_path's _turn_path_cache key both
    already include it — two different origin table sizes can share the
    same literal action-kind path.
    """
    preflop_result = _get_or_solve_preflop_raw(stack_bb, iterations, players=players)
    actions, _node = _resolve_action_path(preflop_result.root, action_kinds)
    path_scenario = derive_ranges_from_path(preflop_result, actions)

    if not isinstance(path_scenario.node, TerminalNode):
        raise ValueError("action_path does not reach a terminal — action isn't capped yet")
    if len(path_scenario.live_positions) < 3:
        raise ValueError(
            f"action_path leaves only {len(path_scenario.live_positions)} live position(s) — "
            "use /solve_flop_from_path for a 2-survivor situation"
        )

    # Known, deliberate gap (M29, same as _query_flop_from_path above):
    # path_scenario.trained isn't surfaced in this endpoint's response.
    capped_ranges = {
        position: _cap_range(range_dict, MAX_MULTIWAY_PATH_QUERY_CLASSES_PER_POSITION)
        for position, range_dict in path_scenario.ranges.items()
    }

    exclude = frozenset(board_cards)
    position_ranges = {
        position: range_from_class_frequencies(range_dict, exclude=exclude)
        for position, range_dict in capped_ranges.items()
    }
    for position, combo_dict in position_ranges.items():
        if not combo_dict:
            raise ValueError(
                f"board {''.join(str(c) for c in board_cards)!r} blocks every combo in "
                f"{position}'s derived (capped) range"
            )

    # M29: postflop_action_order, already N-general — a 2-player caller
    # elsewhere in this file unpacks its first two entries; here the
    # full 3+-entry tuple is exactly what solve_flop_multiway's own
    # `positions` parameter needs.
    postflop_positions = postflop_action_order(preflop_result.config.positions, path_scenario.live_positions)
    effective_stack_bb = path_scenario.stacks[postflop_positions[0]]
    if any(path_scenario.stacks[p] != effective_stack_bb for p in postflop_positions):
        raise RuntimeError(
            "derive_ranges_from_path's own TerminalNode guarantee (equal remaining stacks "
            "across every live position) did not hold — this should be unreachable"
        )

    key = (tuple(action_kinds), players, round(stack_bb), iterations, board_cards, flop_iterations)
    with _flop_multiway_path_lock:
        cached = _flop_multiway_path_cache.get(key)
    if cached is None:
        result = solve_flop_multiway(
            board=board_cards,
            position_ranges=position_ranges,
            pot=path_scenario.pot,
            effective_stack_bb=effective_stack_bb,
            positions=postflop_positions,
            raise_sizes=MULTIWAY_FLOP_RAISE_SIZES,
            max_raises=MULTIWAY_FLOP_MAX_RAISES,
            iterations=flop_iterations,
        )
        with _flop_multiway_path_lock:
            _flop_multiway_path_cache[key] = result
        cached = result

    formatted = format_flop_response(cached, board="".join(str(c) for c in board_cards))
    return {
        "board": formatted["board"],
        "action_path": list(action_kinds),
        "stack_bb": stack_bb,
        "effective_stack_bb": effective_stack_bb,
        "pot": path_scenario.pot,
        "flop_iterations": formatted["iterations"],
        "elapsed_seconds": formatted["elapsed_seconds"],
        "strategy": formatted["strategy"],
        "trained": formatted["trained"],
        "position": formatted["position"],
        "positions": formatted["positions"],
        "players": players,
    }


def _query_turn_from_path(
    preflop_action_kinds: list,
    flop_action_kinds: list,
    turn_card,
    stack_bb: float,
    board_cards: tuple,
    iterations: int,
    turn_iterations: int,
    players: int = 2,
) -> dict:
    """Orchestrates POST /solve_turn_from_path end to end: a real
    (cached, raw) preflop solve -> resolve the client's preflop action
    kinds -> derive_ranges_from_path (M16) -> cap both sides (Finding 1,
    same as /solve_flop_from_path) -> solve_flop_turn (M12), behind its
    own narrowly-keyed cache -> resolve the client's flop action kinds
    against *that* result's own root -> deal the client's real turn
    card -> read whatever real strategy solve_flop_turn already computed
    there. See the module docstring for the full design writeup.

    `players` (M29): part of `_turn_path_cache`'s own key below, for the
    identical collision reason `_query_flop_from_path`'s partition key
    now includes it.
    """
    preflop_result = _get_or_solve_preflop_raw(stack_bb, iterations, players=players)
    preflop_actions, _node = _resolve_action_path(preflop_result.root, preflop_action_kinds)
    path_scenario = derive_ranges_from_path(preflop_result, preflop_actions)
    # Known, deliberate gap: path_scenario.trained isn't surfaced here
    # either — see _query_flop_from_path's identical note above.

    # Ported from library.query_strategy_from_path (bypassed here — its
    # canonical-library machinery doesn't fit this endpoint's per-turn-
    # card query shape) — not specific to that abstraction, still
    # required: derive_ranges_from_path itself does not require the
    # preflop action to have closed.
    if not isinstance(path_scenario.node, TerminalNode):
        raise ValueError("preflop_action_path does not reach a terminal — action isn't capped yet")
    if len(path_scenario.live_positions) != 2:
        # Only reachable once players != 2 is actually possible here
        # (M29) — a multiway-origin preflop path can close with 3+ live
        # positions (e.g. a 3-way pot nobody folds out of), which
        # solve_flop_turn's 2-position-only postflop machinery can't
        # model. Checked explicitly rather than let postflop_action_
        # order's own unpack fail with a confusing "too many values".
        raise ValueError(
            f"preflop_action_path leaves {len(path_scenario.live_positions)} live positions, not 2 — "
            "postflop solving is 2-position only, regardless of the origin table size"
        )
    # M29: postflop_action_order, not a raw positions[0]/[1] unpack —
    # correct at any origin table size, not just heads-up.
    oop_position, ip_position = postflop_action_order(preflop_result.config.positions, path_scenario.live_positions)
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

    turn_solve_key = (
        tuple(preflop_action_kinds),
        round(stack_bb),
        iterations,
        board_cards,
        turn_iterations,
        players,
    )
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
        "players": players,
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
            "trained": {},
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
            "trained": {},
            "pot": turn_node.pot,
            "effective_stack_bb": remaining_stack,
        }

    # Only the FIRST turn decision is ever exposed here (branch.root
    # itself), never a deeper turn-street path — see the module
    # docstring for why that's a deliberate cut, not an oversight.
    strategy = result.strategy_at(turn_node)
    trained = result.trained_hands(turn_node)
    return {
        **response,
        "is_terminal": False,
        "player_to_act": turn_node.player_to_act,
        "strategy": strategy,
        "trained": trained,
        "pot": turn_node.pot,
        "effective_stack_bb": remaining_stack,
    }


def _query_turn_multiway_from_path(
    preflop_action_kinds: list,
    flop_action_kinds: list,
    turn_card,
    stack_bb: float,
    board_cards: tuple,
    iterations: int,
    flop_iterations: int,
    players: int = 3,
) -> dict:
    """Orchestrates POST /solve_turn_multiway_from_path end to end — the
    multiway analog of _query_turn_from_path (M26): a real (cached, raw)
    preflop solve -> resolve the client's preflop action kinds ->
    derive_ranges_from_path (M16, already N-general) -> require a genuine
    3+-live-position terminal (mirrors _query_flop_multiway_from_path's
    own M42 scope boundary — a 2-survivor path stays /solve_turn_from_
    path's own job) -> cap every position's range -> solve_flop_turn_
    multiway (M36), behind its own plain cache -> resolve the client's
    flop action kinds against THAT result's own root -> deal the
    client's real turn card, via ensure_flop_turn_multiway_branch (M44)
    rather than a plain dict lookup — a real, structural difference from
    _query_turn_from_path's own chance_node.branches[turn_card] lookup:
    solve_flop_turn_multiway's chance_data only contains the (terminal,
    card) pairs MCCFR actually happened to sample, not every legal card
    the way the exact solver's chance_data does, so a legal card the
    solve never sampled is built on demand instead of rejected.
    """
    preflop_result = _get_or_solve_preflop_raw(stack_bb, iterations, players=players)
    preflop_actions, _node = _resolve_action_path(preflop_result.root, preflop_action_kinds)
    path_scenario = derive_ranges_from_path(preflop_result, preflop_actions)
    # Known, deliberate gap (M29, same as every other path-based
    # endpoint): path_scenario.trained isn't surfaced here either.

    if not isinstance(path_scenario.node, TerminalNode):
        raise ValueError("preflop_action_path does not reach a terminal — action isn't capped yet")
    if len(path_scenario.live_positions) < 3:
        raise ValueError(
            f"preflop_action_path leaves only {len(path_scenario.live_positions)} live position(s) — "
            "use /solve_turn_from_path for a 2-survivor situation"
        )

    capped_ranges = {
        position: _cap_range(range_dict, MAX_MULTIWAY_TURN_PATH_QUERY_CLASSES_PER_POSITION)
        for position, range_dict in path_scenario.ranges.items()
    }
    exclude = frozenset(board_cards)
    position_ranges = {
        position: range_from_class_frequencies(range_dict, exclude=exclude)
        for position, range_dict in capped_ranges.items()
    }
    for position, combo_dict in position_ranges.items():
        if not combo_dict:
            raise ValueError(
                f"board {''.join(str(c) for c in board_cards)!r} blocks every combo in "
                f"{position}'s derived (capped) range"
            )

    postflop_positions = postflop_action_order(preflop_result.config.positions, path_scenario.live_positions)
    effective_stack_bb = path_scenario.stacks[postflop_positions[0]]
    if any(path_scenario.stacks[p] != effective_stack_bb for p in postflop_positions):
        raise RuntimeError(
            "derive_ranges_from_path's own TerminalNode guarantee (equal remaining stacks "
            "across every live position) did not hold — this should be unreachable"
        )

    turn_solve_key = (
        tuple(preflop_action_kinds),
        players,
        round(stack_bb),
        iterations,
        board_cards,
        flop_iterations,
    )
    with _turn_multiway_path_lock:
        result = _turn_multiway_path_cache.get(turn_solve_key)
    if result is None:
        result = solve_flop_turn_multiway(
            board=board_cards,
            position_ranges=position_ranges,
            pot=path_scenario.pot,
            effective_stack_bb=effective_stack_bb,
            positions=postflop_positions,
            raise_sizes=MULTIWAY_FLOP_RAISE_SIZES,
            max_raises=MULTIWAY_FLOP_MAX_RAISES,
            iterations=flop_iterations,
        )
        with _turn_multiway_path_lock:
            _turn_multiway_path_cache[turn_solve_key] = result

    _flop_actions, flop_node = _resolve_action_path(result.root, flop_action_kinds)
    if not isinstance(flop_node, TerminalNode):
        raise ValueError("flop_action_path does not reach a terminal — action isn't capped yet")

    response = {
        "board": "".join(str(c) for c in board_cards),
        "turn_card": str(turn_card),
        "preflop_action_path": list(preflop_action_kinds),
        "flop_action_path": list(flop_action_kinds),
        "stack_bb": stack_bb,
        "flop_iterations": result.iterations,
        "position": postflop_positions[0],
        "positions": list(postflop_positions),
        "players": players,
        "elapsed_seconds": result.elapsed_seconds,
    }

    if not flop_node.is_showdown:
        # is_showdown is `len(invested) - len(folded) > 1` (game_tree.py),
        # already N-general — a fold-out down to exactly 1 live position
        # at ANY origin table size, not just heads-up. chance_data is
        # only ever populated for a showdown-eligible terminal (both here
        # and in the exact 2-position solver), so this check alone
        # (unlike _query_turn_from_path's own heads-up-only "folded"
        # framing) is both necessary and sufficient here too.
        return {
            **response,
            "is_terminal": True,
            "player_to_act": None,
            "strategy": {},
            "trained": {},
            "pot": flop_node.pot,
            "effective_stack_bb": effective_stack_bb,
        }

    with _turn_multiway_path_lock:
        try:
            branch = ensure_flop_turn_multiway_branch(
                result,
                flop_node,
                turn_card,
                board=board_cards,
                position_ranges=position_ranges,
                positions=postflop_positions,
                effective_stack_bb=effective_stack_bb,
                raise_sizes=MULTIWAY_FLOP_RAISE_SIZES,
                max_raises=MULTIWAY_FLOP_MAX_RAISES,
            )
        except ValueError as exc:
            raise ValueError(f"{turn_card} is not a legal turn card here (already on the board)") from exc
    turn_node = branch.root

    # Recomputed identically to _query_turn_from_path's own remaining_
    # stack formula — see that function's comment for why this must stay
    # hand-in-sync with chance.py's own construction if it ever changes.
    remaining_stack = effective_stack_bb - max(flop_node.invested.values())

    if isinstance(turn_node, TerminalNode):
        # The flop action already put a player fully all-in — chance.py's
        # own design reuses the terminal itself as branch.root there.
        return {
            **response,
            "is_terminal": True,
            "player_to_act": None,
            "strategy": {},
            "trained": {},
            "pot": turn_node.pot,
            "effective_stack_bb": remaining_stack,
        }

    strategy = result.strategy_at(turn_node)
    trained = result.trained_hands(turn_node)
    return {
        **response,
        "is_terminal": False,
        "player_to_act": turn_node.player_to_act,
        "strategy": strategy,
        "trained": trained,
        "pot": turn_node.pot,
        "effective_stack_bb": remaining_stack,
    }


def _query_river_from_path(
    preflop_action_kinds: list,
    flop_action_kinds: list,
    turn_card,
    turn_action_kinds: list,
    river_card,
    stack_bb: float,
    board_cards: tuple,
    iterations: int,
    river_iterations: int,
    players: int = 2,
) -> dict:
    """Orchestrates POST /solve_river_from_path end to end — one street
    further than _query_turn_from_path, whose structure this mirrors
    exactly for the first hop: a real (cached, raw) preflop solve ->
    resolve the client's preflop action kinds -> derive_ranges_from_path
    (M16) -> require exactly 2 live positions -> cap both sides by COMBO
    count (RIVER_PATH_QUERY_MAX_COMBOS_PER_SIDE, not class — see that
    constant's own comment for the measured reason) -> solve_flop_to_
    river (M13), behind its own narrowly-keyed cache -> resolve the
    client's flop action kinds against that result's own root -> deal
    the client's real turn card -> resolve the client's real TURN action
    kinds against the resulting turn-street root (new relative to the
    turn endpoint, which only ever exposes the FIRST turn decision) ->
    deal the client's real river card -> read whatever real strategy
    solve_flop_to_river already computed there.

    `players` (mirrors M29's own precedent): part of `_river_path_
    cache`'s own key, for the identical collision reason `_turn_path_
    cache`'s key already includes it.
    """
    preflop_result = _get_or_solve_preflop_raw(stack_bb, iterations, players=players)
    preflop_actions, _node = _resolve_action_path(preflop_result.root, preflop_action_kinds)
    path_scenario = derive_ranges_from_path(preflop_result, preflop_actions)
    # Known, deliberate gap: path_scenario.trained isn't surfaced here
    # either — see _query_flop_from_path's identical note above.

    if not isinstance(path_scenario.node, TerminalNode):
        raise ValueError("preflop_action_path does not reach a terminal — action isn't capped yet")
    if len(path_scenario.live_positions) != 2:
        # Same restriction _query_turn_from_path already has, for the
        # identical reason: postflop solving here is 2-position only,
        # regardless of the origin table size.
        raise ValueError(
            f"preflop_action_path leaves {len(path_scenario.live_positions)} live positions, not 2 — "
            "postflop solving is 2-position only, regardless of the origin table size"
        )
    oop_position, ip_position = postflop_action_order(preflop_result.config.positions, path_scenario.live_positions)
    oop_stack = path_scenario.stacks[oop_position]
    ip_stack = path_scenario.stacks[ip_position]
    if oop_stack != ip_stack:
        raise RuntimeError(
            "derive_ranges_from_path returned unequal stacks at a terminal node — should be "
            "impossible per game_tree.py's no-side-pots invariant, please report"
        )
    effective_stack_bb = oop_stack

    exclude = frozenset(board_cards)
    hero_range = _cap_range_to_combos(path_scenario.ranges[oop_position], RIVER_PATH_QUERY_MAX_COMBOS_PER_SIDE, exclude)
    villain_range = _cap_range_to_combos(path_scenario.ranges[ip_position], RIVER_PATH_QUERY_MAX_COMBOS_PER_SIDE, exclude)
    if not hero_range:
        raise ValueError(f"board {''.join(str(c) for c in board_cards)!r} blocks every combo in {oop_position}'s derived (capped) range")
    if not villain_range:
        raise ValueError(f"board {''.join(str(c) for c in board_cards)!r} blocks every combo in {ip_position}'s derived (capped) range")

    river_solve_key = (
        tuple(preflop_action_kinds),
        round(stack_bb),
        iterations,
        board_cards,
        river_iterations,
        players,
    )
    with _river_path_lock:
        result = _river_path_cache.get(river_solve_key)
    if result is None:
        result = solve_flop_to_river(
            board=board_cards,
            hero_range=hero_range,
            villain_range=villain_range,
            pot=path_scenario.pot,
            effective_stack_bb=effective_stack_bb,
            positions=(oop_position, ip_position),
            raise_sizes=FLOP_TO_RIVER_RAISE_SIZES,
            max_raises=FLOP_TO_RIVER_MAX_RAISES,
            iterations=river_iterations,
        )
        with _river_path_lock:
            _river_path_cache[river_solve_key] = result

    _flop_actions, flop_node = _resolve_action_path(result.root, flop_action_kinds)
    if not isinstance(flop_node, TerminalNode):
        raise ValueError("flop_action_path does not reach a terminal — action isn't capped yet")

    response = {
        "board": "".join(str(c) for c in board_cards),
        "turn_card": str(turn_card),
        "river_card": str(river_card),
        "preflop_action_path": list(preflop_action_kinds),
        "flop_action_path": list(flop_action_kinds),
        "turn_action_path": list(turn_action_kinds),
        "stack_bb": stack_bb,
        "position": oop_position,
        "positions": [oop_position, ip_position],
        "players": players,
        "river_iterations": river_iterations,
        "elapsed_seconds": result.elapsed_seconds,
    }

    if id(flop_node) not in result.chance_data:
        # Not showdown-eligible at the flop — someone folded there. No
        # turn or river decision to make. Same reasoning as _query_turn_
        # from_path's own identical check (is_showdown is exactly "2+
        # live positions", proven N-general, not a heads-up-only fact —
        # see game_tree.TerminalNode.is_showdown's own definition).
        return {
            **response,
            "is_terminal": True,
            "player_to_act": None,
            "strategy": {},
            "trained": {},
            "pot": flop_node.pot,
            "effective_stack_bb": effective_stack_bb,
        }

    turn_chance_node = result.chance_data[id(flop_node)]
    if turn_card not in turn_chance_node.branches:
        raise ValueError(f"{turn_card} is not a legal turn card here (already on the board, or already dealt)")
    turn_root = turn_chance_node.branches[turn_card].root

    # Same recomputation _query_turn_from_path's own comment already
    # explains (no way to read remaining_stack back off ChanceNode/
    # ChanceBranch directly) — the amount remaining entering the turn.
    remaining_stack_after_flop = effective_stack_bb - max(turn_chance_node.invested.values())

    if isinstance(turn_root, TerminalNode):
        # The flop action already put a player fully all-in — chance.py's
        # own design reuses the terminal itself as branch.root in that
        # case, never populating a real turn decision node, so there's
        # no turn action to resolve and no river decision either.
        return {
            **response,
            "is_terminal": True,
            "player_to_act": None,
            "strategy": {},
            "trained": {},
            "pot": turn_root.pot,
            "effective_stack_bb": remaining_stack_after_flop,
        }

    # New relative to _query_turn_from_path: the turn is a full betting
    # round, so a real river decision needs the client's own turn action
    # path resolved against turn_root, not turn_root itself.
    _turn_actions, turn_node = _resolve_action_path(turn_root, turn_action_kinds)
    if not isinstance(turn_node, TerminalNode):
        raise ValueError("turn_action_path does not reach a terminal — action isn't capped yet")

    # The amount remaining entering the river — turn_node.invested is
    # the TURN street's own fresh (0-based) investment tracking, not
    # cumulative with the flop's, per game_tree.StreetConfig's own
    # per-street reset (see game_tree.py's pot_offset docstring).
    remaining_stack_after_turn = remaining_stack_after_flop - max(turn_node.invested.values())

    if id(turn_node) not in result.chance_data:
        # Folded on the turn — no river decision to make.
        return {
            **response,
            "is_terminal": True,
            "player_to_act": None,
            "strategy": {},
            "trained": {},
            "pot": turn_node.pot,
            "effective_stack_bb": remaining_stack_after_turn,
        }

    river_chance_node = result.chance_data[id(turn_node)]
    if river_card not in river_chance_node.branches:
        raise ValueError(f"{river_card} is not a legal river card here (already on the board, or already dealt)")
    river_node = river_chance_node.branches[river_card].root

    if isinstance(river_node, TerminalNode):
        # Already all-in on the turn — no river decision, just a
        # showdown once the river card is revealed.
        return {
            **response,
            "is_terminal": True,
            "player_to_act": None,
            "strategy": {},
            "trained": {},
            "pot": river_node.pot,
            "effective_stack_bb": remaining_stack_after_turn,
        }

    # A real river decision — the deepest node this endpoint (or any
    # endpoint in this app) exposes.
    strategy = result.strategy_at(river_node)
    trained = result.trained_hands(river_node)
    return {
        **response,
        "is_terminal": False,
        "player_to_act": river_node.player_to_act,
        "strategy": strategy,
        "trained": trained,
        "pot": river_node.pot,
        "effective_stack_bb": remaining_stack_after_turn,
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

    # /solve_river_from_path's own cost (~43s at its own default combo
    # cap) is well past the tax-worth-avoiding bracket the pre-warms
    # above were already accepted for — a real, worse cold-start tax
    # than any of them. Default line: bet/call on the flop, check/check
    # on the turn (keeps both positions live, no all-in), a real turn
    # card and a real, distinct river card.
    try:
        logger.info("pre-warming solve_river_from_path for the default line")
        _query_river_from_path(
            ["raise", "call_or_check"],
            ["raise", "call_or_check"],
            parse_cards("2h")[0],
            ["call_or_check", "call_or_check"],
            parse_cards("9s")[0],
            100.0,
            tuple(parse_cards(DEFAULT_CHAINED_FLOP_BOARD)),
            DEFAULT_ITERATIONS,
            DEFAULT_RIVER_PATH_QUERY_ITERATIONS,
        )
    except Exception:
        logger.exception("pre-warm failed for solve_river_from_path")


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


@app.get("/solve_flop_multiway", response_model=FlopSolveResponse)
async def solve_flop_multiway_endpoint(
    board: str = Query(..., description="Exactly 3 cards, e.g. Jh7d2c"),
    pot: float = Query(10.0, gt=0, description="Pot entering the flop"),
    stack_bb: float = Query(40.0, gt=0, description="Effective stack behind, in big blinds"),
    iterations: int = Query(DEFAULT_FLOP_MULTIWAY_ITERATIONS, gt=0, le=MAX_FLOP_MULTIWAY_ITERATIONS),
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
    iterations: int = Query(DEFAULT_FLOP_TURN_MULTIWAY_ITERATIONS, gt=0, le=MAX_FLOP_TURN_MULTIWAY_ITERATIONS),
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
    iterations: int = Query(DEFAULT_FLOP_TO_RIVER_MULTIWAY_ITERATIONS, gt=0, le=MAX_FLOP_TO_RIVER_MULTIWAY_ITERATIONS),
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
            _query_flop_from_path, request.action_path, request.stack_bb, board_cards, iterations, request.players
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/solve_flop_multiway_from_path", response_model=FlopMultiwayPathQueryResponse)
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
        if len(request.action_path) > MAX_PATH_LENGTH:
            raise ValueError(f"action_path is too long ({len(request.action_path)} > {MAX_PATH_LENGTH})")
        board_cards = tuple(parse_cards(request.board))
        if len(board_cards) != 3:
            raise ValueError(f"board must have exactly 3 cards for a flop, got {len(board_cards)}")
        iterations = request.iterations if request.iterations is not None else DEFAULT_ITERATIONS
        if not 0 < iterations <= MAX_ITERATIONS:
            raise ValueError(f"iterations must be between 1 and {MAX_ITERATIONS}, got {iterations}")
        flop_iterations = (
            request.flop_iterations
            if request.flop_iterations is not None
            else DEFAULT_MULTIWAY_PATH_QUERY_FLOP_ITERATIONS
        )
        if not 0 < flop_iterations <= MAX_MULTIWAY_PATH_QUERY_FLOP_ITERATIONS:
            raise ValueError(
                f"flop_iterations must be between 1 and {MAX_MULTIWAY_PATH_QUERY_FLOP_ITERATIONS}, "
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
        if len(request.action_path) > MAX_PATH_LENGTH:
            raise ValueError(f"action_path is too long ({len(request.action_path)} > {MAX_PATH_LENGTH})")
        iterations = request.iterations if request.iterations is not None else DEFAULT_ITERATIONS
        if not 0 < iterations <= MAX_ITERATIONS:
            raise ValueError(f"iterations must be between 1 and {MAX_ITERATIONS}, got {iterations}")
        return await run_in_threadpool(
            _preflop_walk, request.stack_bb, request.action_path, iterations, request.players
        )
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
            request.players,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/solve_river_from_path", response_model=RiverPathQueryResponse)
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
        if len(request.preflop_action_path) > MAX_PATH_LENGTH:
            raise ValueError(
                f"preflop_action_path is too long ({len(request.preflop_action_path)} > {MAX_PATH_LENGTH})"
            )
        if len(request.flop_action_path) > MAX_PATH_LENGTH:
            raise ValueError(f"flop_action_path is too long ({len(request.flop_action_path)} > {MAX_PATH_LENGTH})")
        if len(request.turn_action_path) > MAX_PATH_LENGTH:
            raise ValueError(f"turn_action_path is too long ({len(request.turn_action_path)} > {MAX_PATH_LENGTH})")
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
        if not 0 < iterations <= MAX_ITERATIONS:
            raise ValueError(f"iterations must be between 1 and {MAX_ITERATIONS}, got {iterations}")
        river_iterations = (
            request.river_iterations if request.river_iterations is not None else DEFAULT_RIVER_PATH_QUERY_ITERATIONS
        )
        if not 0 < river_iterations <= MAX_RIVER_PATH_QUERY_ITERATIONS:
            raise ValueError(
                f"river_iterations must be between 1 and {MAX_RIVER_PATH_QUERY_ITERATIONS}, got {river_iterations}"
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


@app.post("/solve_turn_multiway_from_path", response_model=TurnMultiwayPathQueryResponse)
async def solve_turn_multiway_from_path_endpoint(request: MultiwayTurnPathRequest):
    """The multiway analog of /solve_turn_from_path (M26), closing M42/
    M43's own remaining "turn-depth" open item — for a real preflop path
    that leaves 3+ live positions at the flop (a case /solve_turn_from_
    path structurally can't serve, mirroring /solve_flop_multiway_from_
    path's own M42 scope boundary relative to /solve_flop_from_path)."""
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
        flop_iterations = (
            request.flop_iterations
            if request.flop_iterations is not None
            else DEFAULT_MULTIWAY_TURN_PATH_QUERY_FLOP_ITERATIONS
        )
        if not 0 < flop_iterations <= MAX_MULTIWAY_TURN_PATH_QUERY_FLOP_ITERATIONS:
            raise ValueError(
                f"flop_iterations must be between 1 and {MAX_MULTIWAY_TURN_PATH_QUERY_FLOP_ITERATIONS}, "
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
if FRONTEND_DIST_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST_DIR), html=True), name="frontend")
