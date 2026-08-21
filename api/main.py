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
already too coarse — a single class can expand to up to 12 combos.
RIVER_PATH_QUERY_MAX_COMBOS_PER_SIDE (own comment has the full, twice-
re-measured numbers) was set to 3 combos/side at M46, then doubled to 6
at M49 once M48's ~5-6x hand-evaluator speedup made the same wall-clock
budget afford a meaningfully wider real range. river_iterations' own
cap mirrors MAX_FLOP_TO_RIVER_ITERATIONS' own "==default, zero
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

# M61: constants live in config.py now. Imported into this module's own
# namespace (rather than referenced as `config.X`) so every existing
# read AND every test monkeypatch of `api_main.<CONST>` keeps working
# unchanged — the split moved where these are defined, not how they are
# resolved.
from .config import (
    DEFAULT_CHAINED_FLOP_BOARD,
    DEFAULT_MULTIWAY_FLOP_BOARD,
    DEFAULT_MULTIWAY_PATH_QUERY_FLOP_ITERATIONS,
    DEFAULT_MULTIWAY_TURN_PATH_QUERY_FLOP_ITERATIONS,
    DEFAULT_RIVER_PATH_QUERY_ITERATIONS,
    DEMO_CHAINED_FLOP_HERO_CLASSES,
    DEMO_CHAINED_FLOP_VILLAIN_CLASSES,
    DEMO_FLOP_HERO_CLASSES,
    DEMO_FLOP_VILLAIN_CLASSES,
    DEMO_MULTIWAY_FLOP_CLASSES,
    DEMO_MULTIWAY_FLOP_POSITIONS,
    DEMO_MULTIWAY_HANDS,
    FLOP_QUERY_HERO_CLASSES,
    FLOP_QUERY_ITERATIONS,
    FLOP_QUERY_POT,
    FLOP_QUERY_VILLAIN_CLASSES,
    FLOP_TO_RIVER_MAX_RAISES,
    FLOP_TO_RIVER_RAISE_SIZES,
    FLOP_TURN_MAX_RAISES,
    FLOP_TURN_RAISE_SIZES,
    FRONTEND_DIST_DIR,
    MAX_FLOP_ITERATIONS,
    MAX_FLOP_MULTIWAY_ITERATIONS,
    MAX_FLOP_TO_RIVER_ITERATIONS,
    MAX_FLOP_TO_RIVER_MULTIWAY_ITERATIONS,
    MAX_FLOP_TURN_ITERATIONS,
    MAX_FLOP_TURN_MULTIWAY_ITERATIONS,
    MAX_ITERATIONS,
    MAX_MULTIWAY_PATH_QUERY_CLASSES_PER_POSITION,
    MAX_MULTIWAY_PATH_QUERY_FLOP_ITERATIONS,
    MAX_MULTIWAY_TURN_PATH_QUERY_CLASSES_PER_POSITION,
    MAX_MULTIWAY_TURN_PATH_QUERY_FLOP_ITERATIONS,
    MAX_PATH_LENGTH,
    MAX_PATH_QUERY_CLASSES_PER_SIDE,
    MAX_RIVER_PATH_QUERY_ITERATIONS,
    MAX_TURN_PATH_QUERY_CLASSES_PER_SIDE,
    MULTIWAY_FLOP_MAX_RAISES,
    MULTIWAY_FLOP_RAISE_SIZES,
    MULTIWAY_TABLE_CONFIGS,
    PATH_QUERY_ITERATIONS,
    PREWARM_STACK_DEPTHS,
    RIVER_PATH_QUERY_MAX_COMBOS_PER_SIDE,
)

logger = logging.getLogger("poker_solver.api")

# M61: the cache layer (see caches.py). _SolveCache is imported too —
# tests call _SolveCache.clear_all() through this module.
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
    _multiway_cache,
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


def _cache_key(stack_bb: float, iterations: int) -> tuple:
    return (round(stack_bb), iterations)


def _get_or_solve_multiway(stack_bb: float, players: int) -> StrategyResult:
    """Solves (or returns the cached result of solving) the full
    `players`-max tree once for `stack_bb`, over DEMO_MULTIWAY_HANDS —
    every position's strategy is derived from this single cached
    StrategyResult, so switching `position` in the API/UI never triggers
    a re-solve."""
    key = (round(stack_bb), players)
    with _multiway_cache.lock:
        cached = _multiway_cache.entries.get(key)
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

    with _multiway_cache.lock:
        _multiway_cache.entries[key] = result
    return result


def _get_or_solve_flop(board_cards: tuple, pot: float, stack_bb: float, iterations: int) -> StrategyResult:
    """Solves (or returns the cached result of solving) DEMO_FLOP_HERO/
    VILLAIN_CLASSES' board-legal expansion for one (board, pot, stack_bb,
    iterations) request — cached the same way multiway solves are, so
    switching `position` in the API/UI never triggers a re-solve."""
    key = (board_cards, round(pot, 2), round(stack_bb), iterations)
    with _flop_cache.lock:
        cached = _flop_cache.entries.get(key)
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

    with _flop_cache.lock:
        _flop_cache.entries[key] = result
    return result


def _get_or_solve_flop_turn(board_cards: tuple, pot: float, stack_bb: float, iterations: int) -> StrategyResult:
    """Solves (or returns the cached result of solving) DEMO_CHAINED_FLOP_
    HERO/VILLAIN_CLASSES' board-legal expansion via solve_flop_turn — same
    shape as _get_or_solve_flop, own cache dict (see its module-level
    comment for why a shared one would be unsafe)."""
    key = (board_cards, round(pot, 2), round(stack_bb), iterations)
    with _flop_turn_cache.lock:
        cached = _flop_turn_cache.entries.get(key)
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

    with _flop_turn_cache.lock:
        _flop_turn_cache.entries[key] = result
    return result


def _get_or_solve_flop_to_river(board_cards: tuple, pot: float, stack_bb: float, iterations: int) -> StrategyResult:
    """Same idea as _get_or_solve_flop_turn, via solve_flop_to_river and
    its own (much tighter — see MAX_FLOP_TO_RIVER_ITERATIONS) cache."""
    key = (board_cards, round(pot, 2), round(stack_bb), iterations)
    with _flop_to_river_cache.lock:
        cached = _flop_to_river_cache.entries.get(key)
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

    with _flop_to_river_cache.lock:
        _flop_to_river_cache.entries[key] = result
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
    with _flop_multiway_cache.lock:
        cached = _flop_multiway_cache.entries.get(key)
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

    with _flop_multiway_cache.lock:
        _flop_multiway_cache.entries[key] = result
    return result


def _get_or_solve_flop_turn_multiway(board_cards: tuple, pot: float, stack_bb: float, iterations: int) -> StrategyResult:
    """Same idea as _get_or_solve_flop_multiway, via solve_flop_turn_
    multiway (M36) and its own (more conservative — see MAX_FLOP_TURN_
    MULTIWAY_ITERATIONS) cache."""
    key = (board_cards, round(pot, 2), round(stack_bb), iterations)
    with _flop_turn_multiway_cache.lock:
        cached = _flop_turn_multiway_cache.entries.get(key)
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

    with _flop_turn_multiway_cache.lock:
        _flop_turn_multiway_cache.entries[key] = result
    return result


def _get_or_solve_flop_to_river_multiway(board_cards: tuple, pot: float, stack_bb: float, iterations: int) -> StrategyResult:
    """Same idea as _get_or_solve_flop_turn_multiway, via solve_flop_to_
    river_multiway (M39) and its own cache — see MAX_FLOP_TO_RIVER_
    MULTIWAY_ITERATIONS' own comment for why this endpoint's cap matches
    solve_flop_turn_multiway's rather than solve_flop_to_river's tiny
    2-position ones."""
    key = (board_cards, round(pot, 2), round(stack_bb), iterations)
    with _flop_to_river_multiway_cache.lock:
        cached = _flop_to_river_multiway_cache.entries.get(key)
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

    with _flop_to_river_multiway_cache.lock:
        _flop_to_river_multiway_cache.entries[key] = result
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

    with _flop_query_library.lock:
        result = query_strategy(
            _flop_query_library.entries,
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
    with _preflop_raw_cache.lock:
        cached = _preflop_raw_cache.entries.get(key)
    if cached is not None:
        return cached

    result = solve_preflop(stack_bb=stack_bb, iterations=iterations)

    with _preflop_raw_cache.lock:
        _preflop_raw_cache.entries[key] = result
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


def _range_confidence(path_scenario, position_ranges: dict) -> dict:
    """Per-position summary of `PathScenario.trained` (M29), restricted
    to the classes that ACTUALLY survived capping (M52).

    Deliberately computed over the surviving classes, not the full
    derived range: a caller's advice is only ever built from what got
    solved, so confidence over the 160-odd classes the cap discarded
    would be noise that dilutes the number that matters.

    Summarized per position rather than returned per hand — the shape
    M29/M42/M44 each deferred deciding. A full per-hand map for every
    live position is mostly noise for a caller asking "can I trust this
    advice"; counts plus a boolean answer that directly, and hero's own
    per-hand flag (see `hero_range_trained`) covers the one hand a
    caller actually holds.

    Why it matters, measured not assumed: M29 found a real 6-max path
    whose derived range came back *exactly* uniform — confident-looking,
    fabricated, and silently indistinguishable from a converged one.
    """
    confidence = {}
    for position, combo_dict in position_ranges.items():
        trained_map = path_scenario.trained.get(position, {})
        classes = {_combo_to_class(combo) for combo in combo_dict}
        # A class absent from `trained` never had solving applied to it
        # along this path (e.g. a force-included hero class) — treated as
        # untrained, the conservative reading, never silently as True.
        trained_classes = sum(1 for cls in classes if trained_map.get(cls, False))
        confidence[position] = {
            "trained_classes": trained_classes,
            "total_classes": len(classes),
            "fully_trained": trained_classes == len(classes),
        }
    return confidence


def _combo_to_class(combo) -> StartingHand:
    """The StartingHand class a concrete HandCombo belongs to (M51).

    Safe because HandCombo.__post_init__ (M10) normalizes card_a to the
    higher (value, suit) pair, so card_a.rank is always the high rank.
    No pair special-case is needed: two distinct cards of the same rank
    must differ in suit, so `suited` is already False for every pair.
    """
    return StartingHand(
        combo.card_a.rank,
        combo.card_b.rank,
        suited=combo.card_a.suit == combo.card_b.suit,
    )


@dataclasses.dataclass(frozen=True)
class _PathSituation:
    """Everything the five path-derived endpoints' shared front half
    produces (M50) — the real, solved-and-validated situation a client's
    action path describes, before any street-specific solving happens.

    `capped_scenario` is only populated for a CLASS-level cap (the
    canonical-library path in _query_flop_from_path needs a real
    PathScenario whose `ranges` are still StartingHand-keyed, per
    library.query_strategy_from_path's own documented class-dicts-only
    contract); a combo-level cap (M46's river endpoint) has no
    meaningful class-level equivalent and leaves this None.
    """

    preflop_result: StrategyResult
    path_scenario: object  # solver.PathScenario
    postflop_positions: tuple
    effective_stack_bb: float
    position_ranges: dict  # position -> {HandCombo: weight}
    capped_scenario: object | None
    # M51: True when `hero_combo` was supplied AND survived the cap on
    # its own derived weight; False when it had to be force-included.
    # None when no hero_combo was supplied at all.
    hero_in_range: bool | None = None
    # M52: per-position summary of PathScenario.trained, restricted to
    # the classes that actually survived capping — position -> {"trained
    # _classes", "total_classes", "fully_trained"}. See _range_confidence.
    range_confidence: dict | None = None
    # M52: whether hero's OWN class was trained along the derivation.
    hero_range_trained: bool | None = None


def _derive_path_situation(
    *,
    action_kinds: list,
    stack_bb: float,
    board_cards: tuple,
    iterations: int,
    players: int,
    multiway: bool,
    sibling_endpoint: str,
    max_classes_per_position: int | None = None,
    max_combos_per_position: int | None = None,
    path_field_name: str = "action_path",
    hero_combo=None,
) -> _PathSituation:
    """The shared front half of every path-derived endpoint (M50).

    Extracted from five near-identical orchestrators (_query_flop_from_
    path, _query_flop_multiway_from_path, _query_turn_from_path,
    _query_turn_multiway_from_path, _query_river_from_path) that each
    hand-rolled the same pipeline: a real (cached, raw) preflop solve ->
    resolve the client's bare action kinds into real Actions ->
    derive_ranges_from_path (M16) -> require a real terminal with the
    right live-position count -> cap every position's derived range ->
    expand to board-legal combos -> postflop_action_order (M29) ->
    derive the shared effective stack. Only the SOLVE stage and the
    response shape genuinely differ between those five; everything above
    was duplicated, which is why surfacing path_scenario.trained (a gap
    named and deferred in M29/M42/M44) kept needing a five-place change.

    Deliberately parameterized, not unified away, because these are real
    per-endpoint differences, not incidental drift:
      * `multiway` — exactly 2 live positions (the exact 2-position
        solvers) vs. 3+ (the MCCFR multiway solvers). Each rejects the
        other's case with a message naming `sibling_endpoint`.
      * class-level vs. combo-level capping — see _cap_range_to_combos
        and RIVER_PATH_QUERY_MAX_COMBOS_PER_SIDE for the measured reason
        the river endpoint needs the finer lever.
      * `path_field_name` — the flop endpoints call their own field
        `action_path`, the deeper ones `preflop_action_path`; error text
        names whichever the client actually sent.

    `hero_combo` (M51, None for every pre-M51 caller) is force-included
    in EVERY live position's capped range — deliberately not just the
    acting position's, since which seat hero occupies isn't knowable
    here for the deeper streets (the acting position isn't determined
    until after solving and walking chance_data). The real cost of that
    choice is honest and small: at most one extra combo per position.
    Without it, a hand outside the top-K would be silently absent from
    the very solve meant to advise it.
    """
    if (max_classes_per_position is None) == (max_combos_per_position is None):
        raise RuntimeError("exactly one of max_classes_per_position/max_combos_per_position must be set")

    preflop_result = _get_or_solve_preflop_raw(stack_bb, iterations, players=players)
    actions, _node = _resolve_action_path(preflop_result.root, action_kinds)
    path_scenario = derive_ranges_from_path(preflop_result, actions)

    # Known, deliberate gap (M29/M42/M44): path_scenario.trained — whether
    # each derived-range hand was genuinely backed by real solving along
    # the path, rather than the untrained uniform default — still isn't
    # surfaced in any endpoint's response. It now has exactly ONE place
    # that would need to change to fix it, which was much of the point of
    # this extraction; the response-shape decision it needs is still its
    # own separate work.
    if not isinstance(path_scenario.node, TerminalNode):
        raise ValueError(f"{path_field_name} does not reach a terminal — action isn't capped yet")

    live_count = len(path_scenario.live_positions)
    if multiway and live_count < 3:
        raise ValueError(
            f"{path_field_name} leaves only {live_count} live position(s) — "
            f"use {sibling_endpoint} for a 2-survivor situation"
        )
    if not multiway and live_count != 2:
        # Previously this case reached postflop_action_order's own 2-tuple
        # unpack and surfaced as a bare "too many values to unpack"
        # ValueError (still a 422, but an unhelpful one) in the two flop
        # endpoints; the deeper ones already checked explicitly. Unified
        # here so every endpoint gives the same real explanation.
        raise ValueError(
            f"{path_field_name} leaves {live_count} live positions, not 2 — "
            f"use {sibling_endpoint} for a 3+-survivor situation"
        )

    postflop_positions = postflop_action_order(preflop_result.config.positions, path_scenario.live_positions)
    effective_stack_bb = path_scenario.stacks[postflop_positions[0]]
    if any(path_scenario.stacks[p] != effective_stack_bb for p in postflop_positions):
        raise RuntimeError(
            "derive_ranges_from_path's own TerminalNode guarantee (equal remaining stacks "
            "across every live position) did not hold — this should be unreachable"
        )

    exclude = frozenset(board_cards)
    capped_scenario = None
    if max_classes_per_position is not None:
        capped_ranges = {
            position: _cap_range(range_dict, max_classes_per_position)
            for position, range_dict in path_scenario.ranges.items()
        }
        capped_scenario = dataclasses.replace(path_scenario, ranges=capped_ranges)
        position_ranges = {
            position: range_from_class_frequencies(range_dict, exclude=exclude)
            for position, range_dict in capped_ranges.items()
        }
    else:
        position_ranges = {
            position: _cap_range_to_combos(range_dict, max_combos_per_position, exclude)
            for position, range_dict in path_scenario.ranges.items()
        }

    for position, combo_dict in position_ranges.items():
        if not combo_dict:
            raise ValueError(
                f"board {''.join(str(c) for c in board_cards)!r} blocks every combo in "
                f"{position}'s derived (capped) range"
            )

    hero_in_range = None
    if hero_combo is not None:
        if hero_combo.blocks(frozenset(board_cards)):
            raise ValueError(f"hero_cards {hero_combo} shares a card with the board — impossible to hold")
        # "In range" means hero's own combo survived the cap on its own
        # derived weight, in EVERY live position — not "it's present
        # after we added it". Computed before any force-inclusion below.
        hero_in_range = all(hero_combo in combo_dict for combo_dict in position_ranges.values())
        for combo_dict in position_ranges.values():
            if hero_combo not in combo_dict:
                # Weight it at the range's own minimum rather than an
                # invented constant: present enough to be solved for,
                # never dominating a range it didn't earn a place in.
                combo_dict[hero_combo] = min(combo_dict.values())
        if capped_scenario is not None:
            # The canonical-library path (_query_flop_from_path) solves
            # from capped_scenario's CLASS-level ranges, not from
            # position_ranges — so hero has to be force-included there
            # too, or the force-inclusion above would silently have no
            # effect on exactly that one endpoint. Class-level, not
            # combo-level, because library.query_strategy_from_path's own
            # contract is class-dicts-only (M20's crux design finding:
            # a suit-asymmetric combo dict breaks canonical reuse).
            hero_class = _combo_to_class(hero_combo)
            hero_ranges = {
                position: (
                    range_dict
                    if hero_class in range_dict
                    else {**range_dict, hero_class: min(range_dict.values())}
                )
                for position, range_dict in capped_scenario.ranges.items()
            }
            capped_scenario = dataclasses.replace(capped_scenario, ranges=hero_ranges)

    hero_range_trained = None
    if hero_combo is not None:
        hero_class = _combo_to_class(hero_combo)
        # Trained only if EVERY live position's derivation had real
        # solving for hero's class — the same all-positions reading
        # hero_in_range uses, and conservative for a missing entry.
        hero_range_trained = all(
            path_scenario.trained.get(position, {}).get(hero_class, False)
            for position in position_ranges
        )

    return _PathSituation(
        preflop_result=preflop_result,
        path_scenario=path_scenario,
        postflop_positions=postflop_positions,
        effective_stack_bb=effective_stack_bb,
        position_ranges=position_ranges,
        capped_scenario=capped_scenario,
        hero_in_range=hero_in_range,
        range_confidence=_range_confidence(path_scenario, position_ranges),
        hero_range_trained=hero_range_trained,
    )


def _query_flop_from_path(
    action_kinds: list, stack_bb: float, board_cards: tuple, iterations: int, players: int = 2,
    hero_combo=None,
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
    situation = _derive_path_situation(
        action_kinds=action_kinds,
        stack_bb=stack_bb,
        board_cards=board_cards,
        iterations=iterations,
        players=players,
        multiway=False,
        sibling_endpoint="/solve_flop_multiway_from_path",
        max_classes_per_position=MAX_PATH_QUERY_CLASSES_PER_SIDE,
        hero_combo=hero_combo,
    )
    oop_position, ip_position = situation.postflop_positions
    effective_stack_bb = situation.effective_stack_bb

    partition_key = (tuple(action_kinds), round(stack_bb), iterations, players)
    with _path_query_libraries.lock:
        library = _path_query_libraries.entries.setdefault(partition_key, {})
        result = query_strategy_from_path(
            library,
            situation.preflop_result,
            situation.capped_scenario,
            board_cards,
            iterations=PATH_QUERY_ITERATIONS,
        )

    path_scenario = situation.path_scenario
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
        "hero_in_range": situation.hero_in_range,
        "range_confidence": situation.range_confidence,
        "hero_range_trained": situation.hero_range_trained,
    }


def _query_flop_multiway_from_path(
    action_kinds: list, stack_bb: float, board_cards: tuple, iterations: int, flop_iterations: int,
    players: int = 3, hero_combo=None,
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
    situation = _derive_path_situation(
        action_kinds=action_kinds,
        stack_bb=stack_bb,
        board_cards=board_cards,
        iterations=iterations,
        players=players,
        multiway=True,
        sibling_endpoint="/solve_flop_from_path",
        max_classes_per_position=MAX_MULTIWAY_PATH_QUERY_CLASSES_PER_POSITION,
        hero_combo=hero_combo,
    )
    path_scenario = situation.path_scenario
    position_ranges = situation.position_ranges
    postflop_positions = situation.postflop_positions
    effective_stack_bb = situation.effective_stack_bb

    key = (tuple(action_kinds), players, round(stack_bb), iterations, board_cards, flop_iterations)
    with _flop_multiway_path_cache.lock:
        cached = _flop_multiway_path_cache.entries.get(key)
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
        with _flop_multiway_path_cache.lock:
            _flop_multiway_path_cache.entries[key] = result
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
        "hero_in_range": situation.hero_in_range,
        "range_confidence": situation.range_confidence,
        "hero_range_trained": situation.hero_range_trained,
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
    hero_combo=None,
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
    # MAX_TURN_PATH_QUERY_CLASSES_PER_SIDE, not MAX_PATH_QUERY_CLASSES_
    # PER_SIDE — see that constant's own comment for the real measured
    # reason (solve_flop_turn's cost curve is fundamentally steeper than
    # solve_flop_from_path's own query_strategy-backed one).
    situation = _derive_path_situation(
        action_kinds=preflop_action_kinds,
        stack_bb=stack_bb,
        board_cards=board_cards,
        iterations=iterations,
        players=players,
        multiway=False,
        sibling_endpoint="/solve_turn_multiway_from_path",
        max_classes_per_position=MAX_TURN_PATH_QUERY_CLASSES_PER_SIDE,
        path_field_name="preflop_action_path",
        hero_combo=hero_combo,
    )
    oop_position, ip_position = situation.postflop_positions
    effective_stack_bb = situation.effective_stack_bb
    path_scenario = situation.path_scenario
    hero_range = situation.position_ranges[oop_position]
    villain_range = situation.position_ranges[ip_position]

    turn_solve_key = (
        tuple(preflop_action_kinds),
        round(stack_bb),
        iterations,
        board_cards,
        turn_iterations,
        players,
    )
    with _turn_path_cache.lock:
        result = _turn_path_cache.entries.get(turn_solve_key)
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
        with _turn_path_cache.lock:
            _turn_path_cache.entries[turn_solve_key] = result

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
        "hero_in_range": situation.hero_in_range,
        "range_confidence": situation.range_confidence,
        "hero_range_trained": situation.hero_range_trained,
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
    hero_combo=None,
    turn_action_kinds: list | None = None,
    river_card=None,
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
    situation = _derive_path_situation(
        action_kinds=preflop_action_kinds,
        stack_bb=stack_bb,
        board_cards=board_cards,
        iterations=iterations,
        players=players,
        multiway=True,
        sibling_endpoint="/solve_turn_from_path",
        max_classes_per_position=MAX_MULTIWAY_TURN_PATH_QUERY_CLASSES_PER_POSITION,
        path_field_name="preflop_action_path",
        hero_combo=hero_combo,
    )
    path_scenario = situation.path_scenario
    position_ranges = situation.position_ranges
    postflop_positions = situation.postflop_positions
    effective_stack_bb = situation.effective_stack_bb

    # River depth is requested by supplying BOTH a turn action path and
    # a river card; either alone is a caller error rejected upstream.
    to_river = river_card is not None
    solver = solve_flop_to_river_multiway if to_river else solve_flop_turn_multiway

    # to_river is part of the key: the two solvers produce genuinely
    # different results (chain_to_river populates a second level of
    # chance_fn), so a turn-depth result must never be served to a
    # river-depth query or vice versa — the same collision reasoning
    # every other cache key in this file applies to `players`.
    turn_solve_key = (
        tuple(preflop_action_kinds),
        players,
        round(stack_bb),
        iterations,
        board_cards,
        flop_iterations,
        to_river,
    )
    with _turn_multiway_path_cache.lock:
        result = _turn_multiway_path_cache.entries.get(turn_solve_key)
    if result is None:
        result = solver(
            board=board_cards,
            position_ranges=position_ranges,
            pot=path_scenario.pot,
            effective_stack_bb=effective_stack_bb,
            positions=postflop_positions,
            raise_sizes=MULTIWAY_FLOP_RAISE_SIZES,
            max_raises=MULTIWAY_FLOP_MAX_RAISES,
            iterations=flop_iterations,
        )
        with _turn_multiway_path_cache.lock:
            _turn_multiway_path_cache.entries[turn_solve_key] = result

    _flop_actions, flop_node = _resolve_action_path(result.root, flop_action_kinds)
    if not isinstance(flop_node, TerminalNode):
        raise ValueError("flop_action_path does not reach a terminal — action isn't capped yet")

    response = {
        "board": "".join(str(c) for c in board_cards),
        "turn_card": str(turn_card),
        "river_card": None if river_card is None else str(river_card),
        "preflop_action_path": list(preflop_action_kinds),
        "flop_action_path": list(flop_action_kinds),
        "turn_action_path": list(turn_action_kinds or []),
        "stack_bb": stack_bb,
        "flop_iterations": result.iterations,
        "position": postflop_positions[0],
        "positions": list(postflop_positions),
        "players": players,
        "elapsed_seconds": result.elapsed_seconds,
        "hero_in_range": situation.hero_in_range,
        "range_confidence": situation.range_confidence,
        "hero_range_trained": situation.hero_range_trained,
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

    ensure_kwargs = {
        "position_ranges": position_ranges,
        "positions": postflop_positions,
        "raise_sizes": MULTIWAY_FLOP_RAISE_SIZES,
        "max_raises": MULTIWAY_FLOP_MAX_RAISES,
        "chain_to_river": to_river,
    }
    with _turn_multiway_path_cache.lock:
        try:
            branch = ensure_mccfr_chance_branch(
                result, flop_node, turn_card, board=board_cards,
                effective_stack_bb=effective_stack_bb, **ensure_kwargs,
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

    if not to_river:
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

    # --- One hop further: the river (M53) -------------------------------
    # Structurally identical to the turn hop above, which is exactly the
    # finding: M44 left open whether a SECOND chained hop needs different
    # treatment, and it does not — same ensure_mccfr_chance_branch, one
    # card-richer board, one street-deeper remaining stack.
    _turn_actions, turn_terminal = _resolve_action_path(turn_node, turn_action_kinds)
    if not isinstance(turn_terminal, TerminalNode):
        raise ValueError("turn_action_path does not reach a terminal — action isn't capped yet")

    # The TURN street's own fresh, 0-based investment tracking, not
    # cumulative with the flop's (game_tree.StreetConfig's per-street
    # reset) — the same subtlety _query_river_from_path documents.
    remaining_after_turn = remaining_stack - max(turn_terminal.invested.values())

    if not turn_terminal.is_showdown:
        return {
            **response,
            "is_terminal": True,
            "player_to_act": None,
            "strategy": {},
            "trained": {},
            "pot": turn_terminal.pot,
            "effective_stack_bb": remaining_after_turn,
        }

    with _turn_multiway_path_cache.lock:
        try:
            river_branch = ensure_mccfr_chance_branch(
                result, turn_terminal, river_card, board=board_cards + (turn_card,),
                effective_stack_bb=remaining_stack, **ensure_kwargs,
            )
        except ValueError as exc:
            raise ValueError(f"{river_card} is not a legal river card here (already on the board)") from exc
    river_node = river_branch.root

    if isinstance(river_node, TerminalNode):
        # Already all-in by the turn — nothing left to decide, just a
        # showdown once the river lands.
        return {
            **response,
            "is_terminal": True,
            "player_to_act": None,
            "strategy": {},
            "trained": {},
            "pot": river_node.pot,
            "effective_stack_bb": remaining_after_turn,
        }

    return {
        **response,
        "is_terminal": False,
        "player_to_act": river_node.player_to_act,
        "strategy": result.strategy_at(river_node),
        "trained": result.trained_hands(river_node),
        "pot": river_node.pot,
        "effective_stack_bb": remaining_after_turn,
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
    hero_combo=None,
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
    # Combo-level capping, not class-level like every sibling endpoint —
    # see RIVER_PATH_QUERY_MAX_COMBOS_PER_SIDE's own comment for the
    # measured reason solve_flop_to_river needs the finer lever.
    situation = _derive_path_situation(
        action_kinds=preflop_action_kinds,
        stack_bb=stack_bb,
        board_cards=board_cards,
        iterations=iterations,
        players=players,
        multiway=False,
        sibling_endpoint="/solve_turn_multiway_from_path",
        max_combos_per_position=RIVER_PATH_QUERY_MAX_COMBOS_PER_SIDE,
        path_field_name="preflop_action_path",
        hero_combo=hero_combo,
    )
    oop_position, ip_position = situation.postflop_positions
    effective_stack_bb = situation.effective_stack_bb
    path_scenario = situation.path_scenario
    hero_range = situation.position_ranges[oop_position]
    villain_range = situation.position_ranges[ip_position]

    river_solve_key = (
        tuple(preflop_action_kinds),
        round(stack_bb),
        iterations,
        board_cards,
        river_iterations,
        players,
    )
    with _river_path_cache.lock:
        result = _river_path_cache.entries.get(river_solve_key)
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
        with _river_path_cache.lock:
            _river_path_cache.entries[river_solve_key] = result

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
        "hero_in_range": situation.hero_in_range,
        "range_confidence": situation.range_confidence,
        "hero_range_trained": situation.hero_range_trained,
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


_ADVISE_STREETS = ("preflop", "flop", "turn", "river")

# Per-(street, is-multiway) postflop iteration cap, reusing each sibling
# endpoint's own separately-measured constant rather than inventing one
# blended value — that per-cell measurement work (M24/M26/M42/M44/M46,
# re-tuned at M49) is real and cell-specific. Preflop has no postflop
# leg, so no entry.
_ADVISE_ITERATION_CAPS = {
    ("flop", False): (PATH_QUERY_ITERATIONS, PATH_QUERY_ITERATIONS),
    ("flop", True): (DEFAULT_MULTIWAY_PATH_QUERY_FLOP_ITERATIONS, MAX_MULTIWAY_PATH_QUERY_FLOP_ITERATIONS),
    ("turn", False): (DEFAULT_FLOP_TURN_ITERATIONS, MAX_FLOP_TURN_ITERATIONS),
    ("turn", True): (
        DEFAULT_MULTIWAY_TURN_PATH_QUERY_FLOP_ITERATIONS,
        MAX_MULTIWAY_TURN_PATH_QUERY_FLOP_ITERATIONS,
    ),
    ("river", False): (DEFAULT_RIVER_PATH_QUERY_ITERATIONS, MAX_RIVER_PATH_QUERY_ITERATIONS),
    ("river", True): (
        DEFAULT_FLOP_TO_RIVER_MULTIWAY_ITERATIONS,
        MAX_FLOP_TO_RIVER_MULTIWAY_ITERATIONS,
    ),
}

# The (street, is-multiway) cells /advise deliberately does NOT serve
# yet, each with the real reason — checked BEFORE the cap lookup above,
# so the caller gets this explanation rather than the bare KeyError that
# a missing cap entry would otherwise raise first (a real ordering bug
# caught by this milestone's own test, not by review).
_ADVISE_UNSUPPORTED_CELLS: dict = {
    # Empty as of M53, which filled the last cell — (river, multiway).
    # Kept (rather than deleted) as the declared place any future
    # unsupported cell states its real reason, and because the route's
    # own check reads it unconditionally.
}


def _infer_street(request) -> str:
    """Which street an AdviseRequest describes, from which fields it
    actually carries (M51) — plus rejection of every partial/skipped
    combination, so a client can't silently get advice for a shallower
    street than it thought it asked about.

    Deliberately inferred rather than client-declared: a `street` field
    the client sets independently of its own board/card fields is a
    second source of truth that can disagree with them.
    """
    if request.board is None:
        for field in ("flop_action_path", "turn_card", "turn_action_path", "river_card"):
            if getattr(request, field) is not None:
                raise ValueError(f"{field} was supplied without a board — a preflop query takes neither")
        return "preflop"

    if request.turn_card is None:
        for field in ("turn_action_path", "river_card"):
            if getattr(request, field) is not None:
                raise ValueError(f"{field} was supplied without a turn_card")
        if request.flop_action_path is not None:
            raise ValueError("flop_action_path was supplied without a turn_card — a flop query takes neither")
        return "flop"

    if request.flop_action_path is None:
        raise ValueError("turn_card requires flop_action_path — the flop's action has to close first")

    if request.river_card is None:
        if request.turn_action_path is not None:
            raise ValueError("turn_action_path was supplied without a river_card — a turn query takes neither")
        return "turn"

    if request.turn_action_path is None:
        raise ValueError("river_card requires turn_action_path — the turn's action has to close first")
    return "river"


def _live_position_count(request, iterations: int) -> int:
    """How many positions actually SURVIVE the preflop path (M52 fix).

    Load-bearing, and a real bug before this existed: /advise used to
    pick its 2-position-vs-multiway solver from `request.players` — the
    ORIGIN table size — which is the wrong question. M29 built support
    specifically for the most common real full-ring shape: everyone
    folds and two players see the flop heads-up. That hand has
    `players=6` but must use the EXACT 2-position solver, not MCCFR.
    Choosing on table size routed it to the multiway cell, which then
    correctly refused it — making /advise unusable for exactly the case
    M29 existed to serve.

    Counts from the resolved node's own `folded` set rather than calling
    derive_ranges_from_path: the reach-multiplication that function does
    is real work this question doesn't need, and it raises on a
    fold-out-to-one path that the orchestrators themselves report far
    more clearly. The preflop solve is already cached, so this is a tree
    walk, not a second solve.
    """
    preflop_result = _get_or_solve_preflop_raw(request.stack_bb, iterations, players=request.players)
    _actions, node = _resolve_action_path(preflop_result.root, request.preflop_action_path)
    return sum(1 for p in preflop_result.config.positions if p not in node.folded)


def _advise_preflop(request, iterations: int, hero_combo=None) -> dict:
    """The one /advise cell with no sibling endpoint behind it (M51):
    real preflop strategy at whatever node the action path reaches.

    Note the deliberately INVERTED terminal requirement relative to
    every postflop cell: those need the preflop action to have CLOSED
    (a TerminalNode) before a board is dealt, whereas preflop advice
    needs the opposite — a live DecisionNode with someone still to act.
    A path that already closed has no preflop decision left to advise.
    """
    preflop_result = _get_or_solve_preflop_raw(request.stack_bb, iterations, players=request.players)
    _actions, node = _resolve_action_path(preflop_result.root, request.preflop_action_path)

    if isinstance(node, TerminalNode):
        raise ValueError(
            "preflop_action_path already reaches a terminal — no preflop decision left to advise. "
            "Supply a board for postflop advice, or shorten the path."
        )

    strategy = preflop_result.strategy_at(node)
    trained = preflop_result.trained_hands(node)
    live_positions = [p for p in preflop_result.config.positions if p not in node.folded]
    return {
        "street": "preflop",
        "players": request.players,
        "positions": live_positions,
        "position": node.player_to_act,
        "player_to_act": node.player_to_act,
        "is_terminal": False,
        "pot": node.pot,
        "effective_stack_bb": preflop_result.config.stack_bb - max(node.invested.values()),
        "strategy": strategy,
        "trained": trained,
        "source": "preflop",
        "solve_iterations": preflop_result.iterations,
        "elapsed_seconds": preflop_result.elapsed_seconds,
        # Preflop strategies are keyed by hand CLASS ("AKs"), not by
        # concrete combo ("AsKs") the way every postflop street is — the
        # preflop solver works over the 169-class abstraction (v1's own
        # foundational choice). So hero's lookup key differs by street,
        # and the route must not assume one shape; it reads hero_key.
        # in_range is unconditionally True here: a preflop solve covers
        # every class, so there's no cap for hero to fall outside of.
        "hero_key": None if hero_combo is None else str(_combo_to_class(hero_combo)),
        "hero_in_range": None if hero_combo is None else True,
    }


def _advise(request, street: str, iterations: int, solve_iterations: int, hero_combo, multiway: bool) -> dict:
    """Dispatches one AdviseRequest to whichever sibling orchestrator
    already serves its (street, table size) cell, then normalizes the
    result into AdviseResponse's own shape (M51).

    Deliberately delegates rather than reimplements: every cell's own
    cache, cap constant, and solver choice stays exactly as its sibling
    endpoint already had it — this is a unified FRONT DOOR, not a second
    implementation to keep in sync.
    """
    if street == "preflop":
        return _advise_preflop(request, iterations, hero_combo)

    # Postflop streets key their strategy dicts by concrete combo,
    # unlike preflop's 169-class abstraction handled above.
    hero_key = None if hero_combo is None else str(hero_combo)

    board_cards = tuple(parse_cards(request.board))
    if len(board_cards) != 3:
        raise ValueError(f"board must have exactly 3 cards for a flop, got {len(board_cards)}")

    if street == "flop" and not multiway:
        raw = _query_flop_from_path(
            request.preflop_action_path, request.stack_bb, board_cards, iterations, request.players,
            hero_combo=hero_combo,
        )
        # The canonical library persists only a flattened strategy dict,
        # so per-hand confidence structurally isn't available here — an
        # explicit null, not a silently-omitted field (M28's boundary).
        return {**raw, "trained": None, "source": "library_hit" if raw["hit"] else "library_miss",
                "solve_iterations": None, "is_terminal": False, "player_to_act": raw["position"],
                "street": street, "hero_key": hero_key}

    if street == "flop":
        raw = _query_flop_multiway_from_path(
            request.preflop_action_path, request.stack_bb, board_cards, iterations, solve_iterations,
            request.players, hero_combo=hero_combo,
        )
        return {**raw, "source": "mccfr", "solve_iterations": raw["flop_iterations"],
                "is_terminal": False, "player_to_act": raw["position"], "street": street,
                "hero_key": hero_key}

    turn_cards = tuple(parse_cards(request.turn_card))
    if len(turn_cards) != 1:
        raise ValueError(f"turn_card must have exactly 1 card, got {len(turn_cards)}")

    if street == "turn":
        query = _query_turn_multiway_from_path if multiway else _query_turn_from_path
        raw = query(
            request.preflop_action_path, request.flop_action_path, turn_cards[0], request.stack_bb,
            board_cards, iterations, solve_iterations, request.players, hero_combo=hero_combo,
        )
        return {**raw, "source": "mccfr" if multiway else "exact",
                "solve_iterations": raw.get("flop_iterations", solve_iterations), "street": street,
                "hero_key": hero_key}

    river_cards = tuple(parse_cards(request.river_card))
    if len(river_cards) != 1:
        raise ValueError(f"river_card must have exactly 1 card, got {len(river_cards)}")
    if multiway:
        # M53: the last cell. Reuses the SAME generalized walker the turn
        # cell uses, one hop deeper — see ensure_mccfr_chance_branch for
        # why a second chained hop needed no structurally different
        # treatment, only a chain_to_river passthrough.
        raw = _query_turn_multiway_from_path(
            request.preflop_action_path, request.flop_action_path, turn_cards[0], request.stack_bb,
            board_cards, iterations, solve_iterations, request.players, hero_combo=hero_combo,
            turn_action_kinds=request.turn_action_path, river_card=river_cards[0],
        )
        return {**raw, "source": "mccfr", "solve_iterations": raw["flop_iterations"],
                "street": street, "hero_key": hero_key}
    raw = _query_river_from_path(
        request.preflop_action_path, request.flop_action_path, turn_cards[0], request.turn_action_path,
        river_cards[0], request.stack_bb, board_cards, iterations, solve_iterations, request.players,
        hero_combo=hero_combo,
    )
    return {**raw, "source": "exact", "solve_iterations": raw["river_iterations"], "street": street,
            "hero_key": hero_key}


def _prewarm_common_depths() -> None:
    for depth in PREWARM_STACK_DEPTHS:
        try:
            logger.info("pre-warming solve for stack_bb=%s", depth)
            _get_or_solve_preflop_raw(depth, DEFAULT_ITERATIONS)
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
        # One helper for every table size (M58). _get_or_solve_preflop_raw
        # already delegates to _get_or_solve_multiway when players != 2,
        # so this is genuinely one cache, not a branch that happens to
        # look unified — and formatting here rather than inside a second
        # cache is what lets `position` be honored at EVERY table size.
        # It previously was not: heads-up silently returned first-to-act
        # regardless of what the caller asked for.
        result = await run_in_threadpool(_get_or_solve_preflop_raw, stack_bb, iterations, players)
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
            if path is not None and len(path) > MAX_PATH_LENGTH:
                raise ValueError(f"{field} is too long ({len(path)} > {MAX_PATH_LENGTH})")

        street = _infer_street(request)

        iterations = request.iterations if request.iterations is not None else DEFAULT_ITERATIONS
        if not 0 < iterations <= MAX_ITERATIONS:
            raise ValueError(f"iterations must be between 1 and {MAX_ITERATIONS}, got {iterations}")

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
            "strategy": raw["strategy"],
            "trained": raw.get("trained"),
            "hero": hero,
            "source": raw["source"],
            "solve_iterations": raw.get("solve_iterations"),
            "elapsed_seconds": raw["elapsed_seconds"],
            "range_confidence": raw.get("range_confidence"),
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        # An unsupported (street, table size) cell — e.g. players=6 at a
        # street whose own cap table has no entry. A clear 422, not a 500.
        raise HTTPException(status_code=422, detail=f"unsupported street/table-size combination: {exc}") from exc


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
