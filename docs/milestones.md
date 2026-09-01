# Milestone log

Every milestone this project has shipped, M8 to the present, in order —
what was built, the measured numbers behind each decision, the findings
and corrections made along the way, and what each one deliberately
deferred.

**Split out of `CLAUDE.md` at M65** (audit recommendation #7). That file
is loaded into context at the start of every session, and 4,282 lines of
history were being paid for on every one of them to serve a document
that is consulted by search, not read front-to-back. `CLAUDE.md` now
carries the durable current-state context; this carries the history.

Nothing here was edited in the move — entries are verbatim, only
reordered strictly by milestone number (the append-only original had
drifted slightly out of sequence).

**Corrections are in-place, by design.** Where a later milestone
disproved an earlier one, the earlier entry carries the correction (see
M9's cache-hit-rate note corrected by M33, M54's profiling claim
corrected by M55, and M57's headroom claim corrected by M63). Read an
entry's own corrections before trusting its conclusions.

---

- **M8 — N-player generalization (multiway preflop), validated at 3-handed.**
  `game_tree.py`, `equity.py`, and `cfr.py` are now N-player-general, not
  hardcoded to 2. Heads-up keeps the original exact, deterministic CFR+
  solver as a fast path (`cfr.solve`); 3+ players use External-Sampling
  MCCFR (`cfr.mccfr_solve`) with a lazy, memoized multiway equity cache
  (`equity.MultiwayEquityCache`) — a full N-way equity table is never
  precomputed eagerly, since it's combinatorially infeasible past N=2.
  `solver.solve_preflop` dispatches between the two paths automatically
  based on `GameConfig.positions`'s length.
  - **Known, load-bearing tuning detail:** MCCFR's opponent-action sampling
    intentionally does *not* use an importance-sampling correction — see
    `EXPLORATION_EPSILON`'s docstring in `poker_solver/cfr.py` for why an
    earlier (textbook-unbiased) version of this was reverted after it was
    found to compound multiplicatively across nested opponent decisions at
    N=3, causing high-variance value estimates that CFR+'s regret flooring
    turned actively destructive. Don't reintroduce it without re-reading
    that writeup.
  - **Scope limit, by design:** the API's 3-max demo (`GET
    /solve/{stack_bb}?players=3`) solves over a small curated 8-hand subset
    (`api/main.py`'s `DEMO_MULTIWAY_HANDS`), not the full 169 classes — a
    full 169-hand 3-max MCCFR solve was measured to take well over 10
    minutes even at a modest iteration count, not viable for an interactive
    endpoint. Scaling this up is M9's job.
  - **Frontend:** a table-size toggle (heads-up / 3-max demo) and, in 3-max
    mode, a position selector (BTN/SB/BB) — see `TableModeControl.tsx`. The
    3-max grid is deliberately sparse (only the curated 8 hands are colored,
    the rest gray) since it's a demo, not a full range chart.

- **M9 — scale multiway preflop to 6-max/full ring.**
  - **Two real bottlenecks found and fixed, neither of which was "just turn
    the dial up" as the plan hoped:**
    1. *Eager tree construction.* `build_game_tree` used to build the whole
       tree upfront — fine at N<=3, but every raise re-opens the round for
       every remaining live player, so tree size grows combinatorially with
       both player count and `max_raises` (measured: ~333K terminals for
       6-max, ~8.7M for 9-max at just 3 raises, tens of millions at the
       default 4). Fixed via `game_tree.LazyChildren` — a `DecisionNode`'s
       `children` now builds (and memoizes) each child only when actually
       accessed, so solving pays only for nodes it visits, not the whole
       combinatorial tree. `walk`/`count_terminal_nodes`/`tree_depth` still
       fully materialize a tree when called (that's their point), just not
       during normal solving.
    2. *Equity computation cost.* Even with a lazy tree, MCCFR at N>=6 was
       still impractically slow — profiling traced it to
       `equity.MultiwayEquityCache`: it caches by the *exact* opponent-hand
       tuple, and that cache's hit rate collapses as opponent count grows
       (the space of possible tuples is roughly `hand_pool_size^opponent_count`
       — small enough to reuse heavily at 3-max's 2 opponents, far too large
       at 9-max's 8 for a hit to be likely). Fixed two ways: (a)
       `hand_eval.best_hand_rank_batch` — a NumPy-vectorized hand evaluator
       (kept alongside the original scalar `best_hand_rank`/`rank_five` as
       the trusted reference; cross-validated against it, not independently
       trusted) that batches many (hand, sampled-board) rankings into one
       computation instead of one Python call each; (b)
       `MultiwayEquityCache.traverser_equity_vector` now deals its fixed
       opponents' cards once and reuses that across every candidate hand,
       instead of re-dealing the same opponents from scratch per candidate.
       Together: ~7x faster per cache-miss computation at 9-max, and 6-max's
       *cache-hit rate* also improved enough that a 30K-iteration 6-max
       solve now runs in ~2.5 minutes. 9-max's cache-miss rate stays high
       regardless of raw speed (that's the nature of the combinatorial
       problem, not something more optimization alone fixes), so it ships
       with a much smaller iteration budget and correspondingly noisier
       output — a real, measured tradeoff, not a hidden shortcut.
       **Correction (M33, closing recommendation #6's second half):**
       this cache-hit-collapse framing is real but was incomplete, per
       `docs/full-table-diagnostic-2026-08.md`'s §3.2 — direct
       measurement traced the actual dominant per-iteration cost driver
       at 9-max to `deal_n_hands`'s exponential-time backtracking stall
       on an infeasible opponent-hand tuple (up to 21.6s for one 8-hand
       call), not the cache alone. M27 fixed that stall (an O(N)
       feasibility precheck, ~0.02ms instead of up to 63s), which is
       what actually made 9-max's iteration budget viable to raise above
       a token amount at all — see M27's own entry below for the numbers.
  - **Iteration budgets, by table size** (`api/main.py`'s
    `MULTIWAY_TABLE_CONFIGS`, same numbers `test_solver.py`'s
    `six_max_result`/`nine_max_result` fixtures validate): 3-max 100K
    (unchanged from M8), 6-max 30K (tight convergence, ~2.5 min), 9-max 300
    (real MCCFR, but noisier — per-iteration cost at 9-max proved too
    variable to safely budget a large count for a live endpoint, so this is
    a much smaller, empirically-verified-reliable count, ~1.5 min; only
    AA's fold rate is asserted tightly in tests, not the full hand set the
    way 6-max's is).
  - **Frontend:** `TableModeControl` now offers heads-up / 3-max / 6-max /
    9-max, each with its own position list (`hands.ts`'s
    `MULTIWAY_POSITIONS`, keyed by table size — UTG through BB at 9-max).
    Switching table size defaults to *that size's* first-to-act position
    (UTG at 6/9-max, not a hardcoded BTN) — an early version of this got
    that wrong, caught by a frontend test asserting the actual position
    list, not just that a position selector rendered.

- **M10 — Combo representation + board-aware equity.**
  - `poker_solver/combos.py` — `HandCombo` (a concrete two-card hand,
    order-normalized so equality/hashing don't depend on construction
    order — a real bug here, a classic swap-without-a-temp-variable
    mistake in `__post_init__`, was caught by `test_combos.py` before it
    ever shipped), `all_combos`/`combos_for_class` (reusing `equity.py`'s
    `_suit_pairs_for`, not reinventing suit enumeration), and
    `range_from_class_frequencies` — the bridge from a preflop solve's
    per-class continue-frequency into a postflop range.
  - `poker_solver/board_equity.py` — `build_board_equity_table`, a
    board-aware N×N combo equity table (kept in its own module, not
    folded into `equity.py`, specifically to avoid a circular import
    with `combos.py`). Reuses `hand_eval.best_hand_rank_batch`, batching
    across Monte Carlo runout samples within one matchup. **Measured,
    not assumed:** ~O(N²) in combo count (a 23-combo range built in
    ~1.1s, a 78-combo range in ~18.7s) — fine for the small-to-moderate
    curated ranges this milestone and the next one actually use, would
    take tens of minutes at the full ~1176-combo scale a wide range
    could reach. Fully batching across matchups (not just within one)
    is the natural next step if/when a milestone needs that scale — not
    done yet since nothing does.
  - `poker_solver/cards.py` gained `parse_cards` (a shared "AhKh"/
    "Ts9h2c"-style parser used by `HandCombo.from_str` and the API layer).
  - **Frontend/API:** `GET /equity?hand_a=...&hand_b=...&board=...` — no
    CFR, no caching needed (a single matchup is fast enough to compute
    live), and a standalone equity-calculator UI section below the main
    range grid. Verified live: preflop AA vs KK ≈ 82/18, and on a
    `2c7d9h` board AA's equity rises to ≈ 90/10 (sensible — a dry,
    unpaired board that doesn't help KK). One real bug caught by live
    verification (not by tests): `frontend/vite.config.ts`'s dev proxy
    only forwarded `/solve`, so `/equity` silently fell through to
    Vite's SPA-fallback `index.html` instead of reaching FastAPI —
    fixed by adding `/equity` to the proxy list.

- **M11 — Flop-only game tree + exact CFR solve.**
  - `poker_solver/game_tree.py` — new `StreetConfig` (positions, entering
    `pot`, remaining `stack_bb`, `raise_sizes`, `max_raises` — no blinds,
    no `positions[-2]`/`[-1]` posting semantics, unlike `GameConfig`) and
    `build_street_tree`, reusing `_build` almost unchanged via two small
    shared abstractions added to both configs: `open_size_reference`
    (what the *first* raise is sized off — `big_blind` for `GameConfig`,
    `pot` for `StreetConfig`) and `pot_offset` (the entering pot not
    attributable to any tracked position's `invested`, which starts at 0
    fresh each street — 0.0 for `GameConfig`, `self.pot` for
    `StreetConfig`). One real, documented consequence:
    `TerminalNode.payoff()`'s "always zero-sum" guarantee only holds for
    `pot_offset=0` (preflop) — a postflop street's payoffs correctly sum
    to `pot_offset`, not 0 (the entering pot was already at stake, not
    contributed this street). Confirmed via `grep` that `.payoff()` is
    never actually called by the real solving code (`cfr.py` reads
    `node.pot`/`node.invested` directly), so this was a documentation/
    test-correctness fix, not a solving-correctness one.
  - `poker_solver/cfr.py` — `solve()` gained optional `positions` (default
    `(BTN, BB)`, unchanged) and `initial_reach` params, letting the same
    exact-tensor fast path serve a flop tree (`positions=("OOP","IP")`,
    real ranges as `initial_reach`) with zero new solving code. Internal
    helpers renamed from BTN/BB-specific to generic (`reach_a`/`reach_b`,
    `position_a`/`position_b`). `initial_reach`'s fallback to each hand's
    `combo_weight` is computed lazily, only for a position actually
    missing from `initial_reach` — needed since `combos.HandCombo` (M11's
    hand type) has no `combo_weight` at all, unlike `StartingHand`, and
    `solve_flop` always supplies both positions' real reach anyway.
  - `poker_solver/solver.py` — new `solve_flop(board, hero_range,
    villain_range, pot, effective_stack_bb, ...)`. Hero's and villain's
    ranges are combined into one shared combo pool (required by
    `cfr.solve()`'s single-`hands`/NxN-`equity_table` design); some
    (hero-combo, villain-combo) pairs in that pool inevitably share a
    physical card, which `build_board_equity_table` correctly reports as
    NaN — replaced with a neutral 0.5 before solving, the same "ignore
    blockers between players' hands" approximation the project has
    carried since M1, now also visible at combo (not just class)
    granularity.
  - **Directional sanity, verified, not assumed:** a hero range that's
    purely polarized (nuts + air, nothing in between) can legitimately
    bet/shove *both* extremes at similar rates under real Nash
    equilibrium — confirmed empirically while writing M11's tests, not a
    bug. The robust "value continues, air folds" check instead lives one
    node deeper: facing a bet, a real made hand (top pair + a straight
    draw) folds far less than complete air, at a realistic (not
    deep-stacked-overbet) stack-to-pot ratio — deep stacks relative to
    the pot were measured to collapse *any* hand's facing-a-shove
    decision to "fold almost always," regardless of relative strength,
    which is a real pot-odds fact, not a solver defect, but the wrong
    scenario to assert directional sanity in.
  - **Frontend/API:** `GET /solve_flop?board=...&pot=...&stack_bb=...&
    position=...` — a curated demo hero/villain range, expressed as small
    *hand-class* sets (`DEMO_FLOP_HERO_CLASSES`/`DEMO_FLOP_VILLAIN_CLASSES`,
    unlike `DEMO_MULTIWAY_HANDS`'s fixed combo pool) expanded per-request
    into board-legal combos via `combos.range_from_class_frequencies` —
    necessary since which combos a fixed pool would even mean varies with
    what the board itself blocks. Measured, not assumed: ~2.6s end to end
    for this pool size (3 hero / 4 villain classes, ~30ish combos after
    expansion), dominated by `board_equity`'s table build (~2.5s), not the
    CFR solve (~0.2s) — fine for a live request, cached per (board, pot,
    stack_bb, iterations) the same way multiway solves are cached per
    (stack_bb, players). `FlopSolver.tsx` — a board/pot/stack/position
    form and a sorted per-combo strategy list (not `RangeGrid`'s 169-cell
    layout — a flop combo range is a much smaller, variable-size,
    board-dependent set, ill-suited to that fixed grid).

- **M12 — Flop→turn chance-node chaining.**
  - `poker_solver/chance.py` (new) — `ChanceNode`/`ChanceBranch` +
    `build_chance_node`: takes a showdown-eligible flop terminal (action
    capped, nobody folded) and turns it into one branch per undealt card,
    each with its own board-specific equity table and its own real
    turn-street betting-round tree (`game_tree.build_street_tree`,
    unmodified). `game_tree.py` itself stays completely untouched —
    chance dispatch lives entirely in `cfr.py`'s recursion, fulfilling
    that module's own docstring promise to stay card-agnostic and
    sidestepping a fight with `LazyChildren`'s immutable, lazily-built
    design (pre-walking the tree to attach chance nodes would undo the
    laziness M9 built it for). Only chains flop→turn, one street — the
    river still uses M11's existing trick (averaged inside the turn
    branch's own equity table) one street further out; turn→river
    chaining is mechanically the same machinery, deliberately left for a
    future milestone, same "prove it small first" scope M9's iteration
    budgets and M10/M11's cost write-ups established.
  - **A real bug caught during design, not code review:** an initial
    sketch threaded `chance_fn` unconditionally through every recursive
    call, including inside a chance branch's own turn-street subtree —
    which would let a turn-level showdown terminal re-trigger the
    *flop*-scoped `chance_fn` and deal a "5th street" card off the wrong
    (3-card) board. Fixed by making chance dispatch a per-branch
    on/off switch (`ChanceBranch.chance_fn`, always `None` in M12) rather
    than an ambient one — `cfr._solve_recurse` uses `branch.chance_fn`
    when recursing into a branch, not the parent's — so a turn terminal
    correctly falls through to its (already river-averaged) equity table
    instead of dealing again. Covered by a dedicated regression test
    (`test_solve_does_not_recurse_chance_fn_into_branch_subtrees`) that
    would fail if this were ever threaded through unconditionally again.
  - `poker_solver/cfr.py` — `_solve_recurse`/`solve()` gained optional
    `chance_fn`/`chance_data` params (both default `None`, every M1–M11
    call site unaffected — verified by full-suite rerun, not just
    reasoning). A `ChanceNode`'s value is the *uniform* average of its
    branches' value matrices; no `InfoSetTable`/regret update happens at
    a chance node itself (not a decision point). `chance_data` is
    caller-suppliable and memoizes each distinct showdown terminal's
    built `ChanceNode` across all iterations (built once, not once per
    iteration) — also what lets a caller walk
    `chance_data[id(terminal)].branches[card].root` into a specific next
    card's subtree afterward.
  - `poker_solver/board_equity.py` — `build_board_equity_table` now
    resolves `remaining_needed == 1` (a turn board, only the river left)
    *exactly* (enumerating all ~44-46 possible rivers), not Monte Carlo
    sampled like `remaining_needed >= 2` still is — cheaper and
    noise-free, and load-bearing for M12 since every chance-branch table
    it builds is exactly this case. `_remaining_deck`'s enumeration was
    promoted to a shared `cards.remaining_deck`, reused by both this
    module and `chance.py` rather than duplicated a third time.
  - `poker_solver/solver.py` — new `solve_flop_turn(board, hero_range,
    villain_range, pot, effective_stack_bb, ...)`, mirroring `solve_flop`
    but wiring a `chance_fn` into `solve()` so every showdown-eligible
    flop terminal chains into a real turn tree. `StrategyResult` gained a
    `chance_data` field (empty `{}` for every pre-M12 result).
    `raise_sizes`/`max_raises` apply to both flop and turn streets — one
    deliberate scope cut, not a separate turn-specific sizing menu.
  - **The new approximation, sized precisely, not hand-waved:** chance
    branches get uniform weight (1 / undealt-card count) regardless of
    which cards either player's range already holds — `remaining_deck`
    only excludes the board, not hole cards, so for any combo, exactly 2
    of the ~47 branches deal a card that combo physically holds; its
    equity table correctly reports 0.5 there, but reach weight still
    contributes to the uniform average over that impossible branch. A
    real, small, precisely-bounded bias (~4.3% of branches per combo,
    deterministic given the combo, non-compounding across iterations,
    nets toward neutral rather than a wrong extreme) — distinct from,
    not a restatement of, the project's existing cross-*player*
    NaN→0.5 blocker precedent (`solve_flop`, `MultiwayEquityCache`),
    which is about two different players' hands conflicting, not one
    hand conflicting with the very card being dealt to decide its own
    equity. Fixable later via per-branch reach masking + renormalization;
    out of scope here to keep this one coherent improvement.
  - **Measured, not assumed — and this is why M12 ships engine + tests
    only, no API/frontend slice:** `build_chance_node` alone, at a tiny
    2-combo pool, builds its ~49 branch tables in ~0.05–0.3s (the
    exact-turn-board fix above is what makes this cheap — cross-checked
    against M10's flop-level 23-combo/~1.1s number). But a flop tree can
    have several distinct showdown terminals, each needing its own
    ~49-table chance node, and the cost is genuinely O(N²) in combo-pool
    size (same shape board_equity.py's own module comment already
    flags): at `solve_flop`'s actual demo scale (`DEMO_FLOP_HERO_CLASSES`/
    `DEMO_FLOP_VILLAIN_CLASSES` expanded to ~33 combos, default
    `max_raises=4`), a real measurement came back at **~183 seconds for
    just 50 iterations** (7 distinct chance nodes) — nowhere close to
    viable for a live, on-demand-solved endpoint. Same honesty M9 applied
    to 9-max's smaller iteration budget rather than pretending a slow
    path is fast: `/solve_flop_turn` and a frontend section are deferred
    to a follow-up milestone, not shipped speculatively slow. At the
    tiny fixture scale tests actually use (2 combos, `max_raises=1`), a
    full `solve_flop_turn` runs in ~2.5–3.3s for 20–100 iterations —
    fine for the test suite.

- **M13 — Turn→river chance-node chaining.**
  - `poker_solver/chance.py` — `build_chance_node` gained one new
    parameter, `chain_to_river: bool = False` (default preserves M12's
    exact behavior for every existing call site/test). When `True`, a
    branch whose own street still has real betting left (`remaining_stack
    > 0`) *and* whose own board isn't already a complete 5-card river
    gets its `chance_fn` populated with a closure that deals *that*
    branch's next card the same way — passing `chain_to_river=True` on a
    flop terminal therefore chains flop→turn→river, not just flop→turn,
    since the recursive call keeps forwarding the flag. This is the last
    chance-node hop possible starting from a 3-card flop: a complete
    river board has no cards left to deal. No changes to `cfr.py` or to
    `ChanceBranch`/`ChanceNode`'s shape were needed — see below.
  - **A correctness pitfall, prevented structurally, not just tested
    for:** an all-in-already branch (`remaining_stack == 0`, `root` reused
    as `terminal`) must never get a populated `chance_fn` — its own
    equity table (built one card richer than the calling board) already
    correctly averages over however many community cards remain via
    `board_equity.py`'s own `remaining_needed` handling, so a second
    explicit chance dispatch on top of that would double-process the same
    physical terminal against two inconsistent runout distributions, a
    real equilibrium-correctness bug, not just wasted work. Fixed by
    setting `chance_fn = None` *inside the same `if remaining_stack == 0`
    branch that already decides `root = terminal`* — the same `if/else`,
    not two independently-maintained decisions that could drift apart —
    rather than a separate identity check applied after the fact.
    Covered by a dedicated regression test at both the `chance.py` level
    (direct `build_chance_node` calls) and the `solver.py` level (the
    same guard holding through the real `solve_flop_to_river` call path,
    using this milestone's own tiny fixture's naturally-occurring
    all-in-at-the-flop line).
  - **A second, independent pitfall, also caught and covered:** each
    branch's river-dealing closure captures its own board/remaining-stack
    via default-argument binding (`lambda t, _b=next_board,
    _s=remaining_stack: ...`), not a bare closure over the loop variables
    — without it, every branch's closure would share the *last* loop
    iteration's board/stack by reference (Python's closure late-binding
    behavior), so every branch would silently deal its river off the
    wrong board once actually invoked. Regression-tested by invoking two
    different branches' own closures and confirming they deal from
    different, correctly-excluded card sets.
  - `poker_solver/solver.py` — new `solve_flop_to_river(...)`, identical
    to `solve_flop_turn` except its `chance_fn` closure passes
    `chain_to_river=True`. No `StrategyResult` field changes:
    `chance_data` (added in M12) is already generic, and traced (not
    assumed) to end up flat but two levels deep — `cfr._solve_recurse`
    forwards the same `chance_data` dict object unchanged at every
    recursion depth, so a turn-level showdown terminal's own chance
    dispatch memoizes into the *same* dict a flop-level one does, keyed
    by its own `id()`. Reaching the river level from a result is the
    identical one-hop-further pattern:
    `chance_data[id(some_turn_terminal)].branches[some_river_card].root`.
    Confirmed with a dedicated `cfr.py` test that hand-builds a chance
    node nested two levels deep (pure dataclasses, no `chance.py`
    involved) and checks both levels land in one dict — proving `cfr.py`
    needed zero changes, not just asserting it.
  - **Measured, not assumed:** the tiny fixture (same one `solve_flop_
    turn` uses — 1 hero combo × 1 villain combo, `max_raises=1`) runs a
    full `solve_flop_to_river` in ~4.3s at 20 iterations — fine for the
    test suite, so `DEFAULT_FLOP_TO_RIVER_ITERATIONS` is kept at 20
    rather than mirroring `DEFAULT_FLOP_TURN_ITERATIONS`'s 200 (measured:
    100 iterations here cost ~55s — chance-node construction cost doesn't
    scale linearly with iteration count the way that default assumed, so
    there's no reason to pay for a larger number nothing is tuned
    against). At demo scale (~33 combos, `max_raises=3`), real
    (not extrapolated) component measurements — one flop-level
    `build_chance_node(chain_to_river=True)` call: ~20.9s; one single
    river-level hop (one branch's populated `chance_fn`, invoked once):
    ~3.4s — combined with the realistic count of distinct turn-level
    showdown terminals a full flop tree would actually visit (~2,400,
    reasoning from M12's own 7-flop-terminal/49-branch numbers) imply a
    full demo-scale solve would cost **on the order of two-plus hours**
    just for river-level chance-node construction — even more decisively
    ruling out a live endpoint than M12's own 183-second flop→turn
    finding one milestone earlier. **Ships engine + tests only, no
    API/frontend slice, same as M12** — not a deferred-pending-
    measurement question this time, a confirmed one.

- **M14 — Live `/solve_flop_turn` and `/solve_flop_to_river` endpoints.**
  Revisits M12/M13's "too slow to expose live" finding at a *much*
  smaller curated pool, sized from real measurement rather than
  assumption, and wires both up live — two product decisions made with
  the user before scoping: ship both endpoints this milestone (not
  deferring the slower one, same honesty precedent as M9's 9-max
  preflop endpoint), and `/solve_flop_turn`'s demo tree uses
  `max_raises=2` (one real sized bet + all-in), not the faster-but-
  thinner `max_raises=1`.
  - `api/main.py` — `DEMO_CHAINED_FLOP_HERO_CLASSES`/`DEMO_CHAINED_FLOP_
    VILLAIN_CLASSES` (one hero class, one villain class — 12 combos),
    **shared** between both new endpoints (not two separate pools) so a
    frontend "runout depth" selector compares the *same* matchup at
    different depths, and deliberately separate from M11's own
    `DEMO_FLOP_HERO_CLASSES`/`..._VILLAIN_CLASSES` (which stays serving
    only `/solve_flop`, unchanged). `max_raises`/`raise_sizes` are fixed
    per-endpoint module constants, never query params — same reasoning
    the existing demo classes already establish (an unbounded-cost door
    otherwise). Two new cache dicts (`_flop_turn_cache`/`_flop_to_river_
    cache`), deliberately *separate* from each other and from
    `_flop_cache` — the cache key omits `max_raises`/the demo pool
    because those are fixed constants, which is only safe *because*
    each endpoint has its own dict (a shared one could let an identical
    key collide between two endpoints with different `max_raises`).
  - **Measured, not assumed — including a real surprise the two-board
    sample size caught:** at the 12-combo pool, `solve_flop_turn`
    (`max_raises=2`) measured ~18-26s across two different real boards,
    cost close to flat across iteration count (every chance node is
    built on iteration 1 regardless — confirmed at the chosen cap, 2000
    iterations, ~59s). `solve_flop_to_river` (`max_raises=1`) measured a
    much wider, genuinely board-dependent ~63-105s *at its default
    iteration count alone* — on one board its cost stayed flat across
    iteration count, on the other it scaled close to linearly (50 iters
    ~145s, 200 iters ~218s). Rather than trust either board's shape to
    generalize, `MAX_FLOP_TO_RIVER_ITERATIONS` is set equal to its own
    default (20) — no headroom above it at all, so `iterations` can only
    ever request a faster, noisier result on this endpoint, never a
    slower one. Also observed directly (not asserted in tests, just
    honest to note): during the background pre-warm window right after
    server startup, a concurrent live request for `solve_flop_to_river`
    measured ~168s — real CPU contention with the in-progress pre-warm
    thread (the same general "pre-warming costs background CPU" tradeoff
    the project has always accepted, just far more visible here given
    how expensive one solve already is), not a new bug; steady-state
    (post-pre-warm) cost matches the ~63-105s range above.
  - **Pre-warming**: unlike `/solve_flop` (~2.6s, never pre-warmed —
    not worth the complexity), one instance of each new endpoint is
    pre-warmed against the frontend's own default board/pot/stack
    (`FlopSolver.tsx`'s `DEFAULT_BOARD`/`DEFAULT_POT`/`DEFAULT_STACK_BB`,
    cross-referenced in a comment on both sides so they don't silently
    drift apart) — ~26s/~63-105s is a meaningfully worse cold-start tax
    on a user's very first, overwhelmingly-likely-default-valued click.
  - `poker_solver/strategy_format.py` — `format_flop_response` reused
    verbatim (docstring-only edit noting it's shape-agnostic across
    solve depth): every field it reads is present identically on a
    `solve_flop`/`solve_flop_turn`/`solve_flop_to_river` result, since
    all three only ever expose the *flop*-level strategy through it.
    `chance_data` (the turn/river tree) stays invisible at this response
    shape — M14 exposes only the improved flop-level number those
    deeper solves produce, not an interactive turn/river tree explorer
    (a materially bigger, separate feature).
  - **Frontend:** extended `FlopSolver.tsx` in place with a "runout
    depth" selector (Flop only / Flop + turn / Flop + turn + river)
    rather than three near-duplicate components — the board/pot/stack/
    position inputs and per-combo rendering are already 100% shared.
    `api.ts`'s `fetchFlopStrategy` gained a leading `depth` parameter
    dispatching to the right endpoint via a lookup table. Depth-specific
    button/loading copy (e.g. "Solving flop + turn + river (up to about
    two minutes)…") is shown persistently near the button, not just
    while loading, so a 60s+ wait isn't a surprise sprung only after
    clicking; switching depth clears any stale result/error from a
    different depth. Live-verified in the browser end to end for all
    three depths: correct URL routing, correct loading/button copy,
    correct clearing on depth switch, correct final rendering (real bet
    sizing at the turn depth, push/fold-only structure at the river
    depth, accurate `elapsed_seconds`).

- **M15 — Preflop→flop range handoff (engine only).**
  `combos.range_from_class_frequencies` has documented itself, since
  M10, as "the bridge from a preflop solve's per-class continue-
  frequency into a postflop range" — but nothing actually produced that
  frequency dict from a real `solve_preflop` result until now; every
  postflop demo used a hand-picked, hardcoded range instead. M15 closes
  that gap for the single most common way a pot actually reaches a
  flop: one player opens, one player calls, everyone else (if any)
  already folded before either acted. Engine only, no `api/main.py`/
  frontend changes this milestone — same "prove it before deciding the
  API shape" pattern M12/M13 followed before M14 resolved their own
  open question.
  - `poker_solver/solver.py` — `StrategyResult.continuing_frequencies
    (node, action_kind=None)`: hand -> frequency, deliberately keyed by
    the actual `StartingHand` *objects* from `self.hands`, not the
    string labels `strategy_at` returns — required because
    `range_from_class_frequencies` reads `hand.high_rank`/`.low_rank`
    off its keys, and `strategy_at`'s own output discards the object
    (keeps only `str(hand)`, with the string→object parser long since
    removed as dead code). `action_kind=None` sums every non-fold
    action ("1 - fold probability," the continue-frequency
    `range_from_class_frequencies`'s own docstring already
    anticipated); `action_kind=<a kind constant>` isolates one specific
    action's frequency instead — load-bearing for `derive_flop_scenario`
    below, not just a nicety: a single node can have both a sized
    `RAISE` and an `ALL_IN` simultaneously (`game_tree._build` adds
    both independently), each leading to a *different* pot, so summing
    them would misattribute a hand that prefers jamming into a range
    meant to represent the sized raise's own pot specifically.
  - `FlopScenario` (new dataclass) + `derive_flop_scenario(result,
    raiser_position, caller_position)`: derives `raiser_range`,
    `caller_range` (`continuing_frequencies` with `action_kind=RAISE`/
    `CALL_OR_CHECK` respectively — not the simpler default, for the
    same pot-consistency reason above), `pot`, and `effective_stack_bb`
    — each ready to feed straight into `solve_flop`/`solve_flop_turn`/
    `solve_flop_to_river`'s existing parameters unchanged. Rejects a
    3+ player `result` (multiway pots out of scope this milestone),
    either position missing from `result.config.positions`, no sized
    raise available, and a `caller_position` that isn't the position
    that actually acts right after the raise.
  - **A real bug caught during design, not code review:** the initial
    sketch found the raiser's node via `node_for_position(raiser_
    position)` — but that method walks `call_or_check` from root
    ("everyone before you limped"), so a non-first-to-act
    `raiser_position` would silently derive a scenario for *raising
    over an earlier limp* (a materially different, out-of-scope line)
    instead of failing loudly. Fixed by using `result.root` directly
    and explicitly checking `raiser_position == result.root.
    player_to_act` first, raising `ValueError` otherwise.
  - **A second real finding, from testing, not assumed:** an initial
    end-to-end test asserted BB's *calling* frequency (`caller_range`)
    is higher for AA than for 72o facing an open-raise — measured
    instead to be the reverse (AA's own call-frequency ≈0.005, 72o's
    ≈0.19, in a 3-hand toy pool) — not a bug: a premium hand facing a
    raise correctly prefers to 3-bet/jam rather than flat-call, so most
    of its non-fold mass goes to `RAISE`, not `CALL_OR_CHECK`, exactly
    real-poker intuition once actually measured. The robust directional
    check instead uses `continuing_frequencies(caller_node)` (overall
    fold-vs-continue, `action_kind=None`) at the same node, which
    behaves the way naive intuition expects — `caller_range` itself is
    correct as `CALL_OR_CHECK`-specific per the design above; the test's
    original *expectation* was wrong, not the code.
  - **Tested end to end, not just structurally:** a real (small, fast)
    `solve_preflop` → `derive_flop_scenario` → `combos.
    range_from_class_frequencies` → real `solve_flop` pipeline, asserting
    AA's combos carry far more weight than 72o's in the resulting range
    — proving real numbers flow through every stage, not that a
    hardcoded range still happens to work.

- **M16 — Arbitrary action-path range derivation.** `derive_flop_
  scenario` (M15) only modeled one fixed 2-step line (raiser opens,
  caller calls). M16 generalizes it: given an **arbitrary sequence of
  actions** from a tree's root, correctly derive every still-live
  position's range at the resulting node, plus pot/stack state — the
  foundational piece for the v3 vision above (a real hand can reach any
  node via any sequence of actions, and a player can act more than once
  along the way).
  - `poker_solver/solver.py` — `PathScenario` (dataclass: `node`,
    `live_positions`, `ranges`, `pot`, `stacks`) + `derive_ranges_from_
    path(result, action_path)`. Walks `result.root` applying each
    `Action` in `action_path` via `node.children[action]`, same
    `LazyChildren` single-step access `derive_flop_scenario` already
    used — no new tree-walking mechanism. Raises `ValueError` for: an
    action not actually legal at the step it's applied to (`Action`
    equality is exact on `kind` *and* `size`, not fuzzy-matched by kind
    alone); the path continuing past a `TerminalNode`; fewer than 2 live
    positions at the end. **Not** an error: the path ending at a
    `DecisionNode` (someone else's turn still to come) — a real feature,
    not a gap, since it lets a caller ask "what does this look like
    right here, mid-round," not just at a fully-resolved endpoint.
  - **The core mechanism, verified against `cfr.py`'s real reach-
    accumulation code, not assumed:** a position that acts more than
    once along a path needs the *product*, in order, of `continuing_
    frequencies(node, action_kind=<action taken>)` across each of their
    own nodes — not a single node's reading. This is exactly how
    `cfr._solve_recurse`'s own `reach_a`/`reach_b` tensors accumulate
    during real solving (`reach_a * strategy[:, a_idx]` only at
    `position_a`'s own nodes, chained down the tree) — `continuing_
    frequencies` at any one node is a *conditional* frequency (given the
    range already reached that node), so multiple such nodes compose
    multiplicatively, the same way conditional probabilities always do.
    Confirmed with a hand-built-`node_data` test at a config where BTN
    opens, BB 3-bets, BTN calls (BTN acts twice): the derived range for
    one hand equals the *product* of two independently-chosen per-node
    frequencies, not either one alone.
  - **Built fully N-player-general**, even though today's only real
    consumer (`solve_flop`/`solve_flop_turn`/`solve_flop_to_river`)
    stays 2-position-hardcoded — deliberately different from M8/M9's own
    "multiway only when actually needed" discipline, which was about not
    paying for genuinely expensive new solving infrastructure
    speculatively. Nothing here is expensive or new: every piece this
    function touches (`game_tree.py`, `continuing_frequencies`,
    `node.folded`/`node.invested`) is already N-general, so restricting
    it to 2 players would have been an artificial regression relative to
    what it's built on, not a "don't build what nothing consumes" call.
    Proven, not just asserted: reuses the existing `three_max_result`
    fixture (no new slow solve) for a 3-handed path where all three stay
    live, and one where a position folds mid-path.
  - `derive_flop_scenario` is now a thin wrapper around `derive_ranges_
    from_path` — all five of its existing `ValueError` checks stay
    unchanged (they're `FlopScenario`-specific semantic constraints,
    e.g. "caller must be the very next actor," not generic path
    illegality), then the mechanical walk itself is delegated rather
    than reimplemented. Every M15 test re-runs unmodified against this
    refactor — the reuse-safety net the refactor call depended on.
  - **Tested end to end at the new, longer path length**: real (small,
    fast) `solve_preflop` → a genuine 3-step path (open, 3-bet, call) →
    real combo expansion → real `solve_flop`. One more real, honestly-
    reported surprise along the way: the naive "premium hand continues
    most" comparison doesn't hold for BTN's *compound* (open-and-call-
    the-3-bet) range specifically — AA's own weight there is tiny,
    because facing a 3-bet, AA prefers to jam rather than flat-call
    (the same "premium hands don't just call" pattern M15 already found,
    one street of aggression deeper). The robust check instead compares
    KK (a real flatting hand in this spot) against trash.

- **M17 — Card abstraction primitive (Phase 1 of the real-time-speed
  roadmap).** See "The real-time-speed roadmap" below for why this
  exists and what it's the first step toward — recorded here in the
  usual per-milestone format for the mechanics of what shipped.
  - `poker_solver/abstraction.py` (new) — `HandBucket`/`BucketedPool` +
    `compute_combo_strengths`/`build_hand_buckets`/`build_bucket_
    equity_table`/`bucket_equity_error`. The bucketing signal is each
    combo's mean same-board equity against the rest of its own pool
    (`build_board_equity_table`'s row, `nanmean`'d), binned into
    equal-frequency buckets — the same pool doubling as its own
    reference range is deliberate (see the module docstring), not an
    approximation to fix: what matters for bucketing one hero/villain
    matchup is relative strength *within that matchup's own pool*.
    **Deliberately not wired into `solve_flop`/`solve_flop_turn`/
    `solve_flop_to_river`/`cfr.solve()` yet** — ships as a measured
    primitive first (mirrors M10's `combos.py`/`board_equity.py`
    shipping before M11 wired them into a real solve), since wiring
    straight into live solving would stack three unmeasured unknowns
    at once (signal fidelity, bucket-count/accuracy tradeoff, whether
    CFR behaves sanely over lossy bucket-aggregated input) with no
    checkpoint between them — the same mistake the discarded batching
    attempt made, just with a silent-wrong-strategy-output failure mode
    instead of a cheaply-discovered slow one.
  - A real efficiency bug caught during implementation, not code
    review: the first version of `build_hand_buckets` computed and
    then discarded its internal equity table, forcing any caller who
    also needed it (e.g. for `build_bucket_equity_table`/`bucket_
    equity_error` — the overwhelmingly common case, since accuracy
    measurement is half the point of this module) to rebuild the same
    N×N table a second time at real cost. Fixed by having `BucketedPool`
    itself carry `source_combos`/`equity_table` — the exact pairing
    `compute_combo_strengths` already produced internally — so
    `build_bucket_equity_table`/`bucket_equity_error` read them off the
    pool instead of taking them as separate (and separately-payable, or
    accidentally mismatched) parameters.
  - **Measured, not assumed — and this measurement itself is the real
    finding of this milestone:** at the same 23/~85/300-combo
    checkpoints established this session, bucketing landed at roughly
    **break-even** with the plain `build_board_equity_table` baseline
    (23 combos: ~1.0s baseline vs ~1.0-1.0s bucketed; ~85 combos:
    ~23.9s vs ~28.0-28.2s, i.e. *slower*; 300 combos: ~327s vs
    ~339-341s, also slower) — not the speedup this phase's own name
    suggests. This is expected once traced through, not a failure:
    `build_hand_buckets` still has to build the *full* N×N equity table
    first, to derive the per-combo bucketing signal itself — bucketing
    cannot skip that step, only add to it. The real payoff this
    milestone sets up but doesn't yet deliver is downstream: a future
    CFR solve running its own O(N²)/O(N) tensor operations over B
    buckets instead of N combos, a real `(N/B)²`-shaped reduction in
    *that* cost — not measurable until the wiring milestone this one
    deliberately defers. Accuracy, measured at the same checkpoints:
    mean absolute error stays low even at modest bucket counts (e.g.
    ~85 combos at B=16: MAE≈0.041, max AE≈0.42) and falls further as B
    grows, giving the next milestone real numbers to size a bucket
    count against instead of guessing.

- **M18 — Wire card abstraction into a real solve (Phase 1b of the
  real-time-speed roadmap).** Answers the question M17 explicitly
  deferred: does a CFR solve running over B buckets instead of N combos
  actually deliver the `(N/B)²`-shaped tensor-cost reduction M17
  predicted? Measured directly this time, not extrapolated — and the
  answer is no, for a specific, traceable reason recorded below.
  - `poker_solver/abstraction.py` — `HandBucket.__str__` (`"bucket{id}
    (n=..., strength=...)"`) gives every bucket the same `str(hand)` ->
    dict-key contract `cfr.solve()`/`StrategyResult.strategy_at` already
    rely on for `HandCombo`/`StartingHand`. New `bucket_reach_vector
    (bucketed_pool, range_dict)` — per-bucket reach weight, summing one
    side's own range dict over each bucket's members (`.get(combo,
    0.0)`, not a raw lookup — a bucket's members can include combos
    entirely absent from one side's range).
  - **The dual-reach subtlety, confirmed before writing any solving
    code, not discovered by a failing test:** `solve_flop`'s reach story
    is two-sided — independent `hero_reach`/`villain_reach` vectors over
    one combined combo pool. `HandBucket.weight` (from M17's
    `build_hand_buckets`) is only a *single* aggregate, built from
    whatever one `combo_weights` dict it was called with. A bucket built
    over hero+villain's combined pool can contain combos that are
    "mostly hero's" and combos that are "mostly villain's," so
    `.weight` can't serve as either side's own CFR reach vector — using
    it directly would silently blend the two positions' ranges inside
    a shared reach number, corrupting the solve rather than merely
    losing precision. Fixed by computing `hero_bucket_reach`/
    `villain_bucket_reach` independently via `bucket_reach_vector`,
    never touching `.weight` for this purpose (confirmed against
    `abstraction.py`'s actual code, not assumed, that nothing else reads
    `.weight` downstream of `build_hand_buckets`).
  - `poker_solver/solver.py` — new `solve_flop_abstracted(board,
    hero_range, villain_range, pot, effective_stack_bb, num_buckets,
    ...)`, identical to `solve_flop` except the combined combo pool is
    first bucketed via `build_hand_buckets` (weighted by each combo's
    *combined* hero+villain weight — inert for bucket membership itself,
    load-bearing for `build_bucket_equity_table`'s weighted aggregate),
    then `cfr.solve()` runs over `bucketed_pool.buckets` and a bucket-
    level equity table instead of real combos. Returns a `StrategyResult`
    whose `hands` are `HandBucket` instances. New `expand_bucket_strategy
    (bucket_strategy, bucketed_pool)` fans a bucket-keyed strategy dict
    back out to real combos, every member inheriting its bucket's
    strategy verbatim — what a real caller actually wants ("what does my
    exact combo do," not "what does bucket 5 do").
  - **Measured, not assumed — and the real finding of this milestone:**
    at the same 23/~85-combo checkpoints M17 used (now with a real
    two-sided `solve_flop` vs. `solve_flop_abstracted` comparison,
    timing equity-build *and* CFR solve together, `DEFAULT_FLOP_
    ITERATIONS`): 23 combos — baseline `solve_flop` 1.21s vs. B=8 1.09s
    (1.11x) vs. B=16 1.09s (1.11x); ~85 combos — baseline 25.84s vs.
    B=8 27.06s (0.95x, *slower*) vs. B=16 26.71s (0.97x) vs. B=32
    26.67s (0.97x). **No meaningful speedup at either scale** — roughly
    break-even at best, slightly slower at 85-combo scale. Tracing why
    confirms M17's own finding rather than contradicting it: at 85
    combos, `solve_flop`'s own equity-table build alone already costs
    ~23.9s (M17's own number) out of this milestone's 25.84s total —
    equity-table construction is the overwhelming majority of total
    cost at these scales, and bucketing only ever *adds* a bucket-table
    build on top of the same full N×N equity table (still required to
    derive the bucketing signal itself), so shrinking N for the CFR
    tensor step barely moves a number that step was never dominating.
    The `(N/B)²` CFR-cost reduction M17 predicted is real *in isolation*
    but not the actual bottleneck at any scale measured so far.
  - **Accuracy, measured via strategy-level total-variation distance
    (mean/max) against the real `solve_flop` result, compared directly
    to M17's own equity-level MAE at matching bucket counts:** 23
    combos — B=8 mean_TV=0.108/max_TV=0.497 (vs. M17's equity MAE=0.049/
    maxAE=0.338 at the same B) and B=16 mean_TV=0.0005/max_TV=0.002 (vs.
    equity MAE=0.013/maxAE=0.045); ~85 combos — B=8 mean_TV=0.419/
    max_TV=0.988, B=16 mean_TV=0.260/max_TV=0.646, B=32 mean_TV=0.149/
    max_TV=0.681 (vs. equity MAE=0.060/0.041/0.030 respectively).
    **Strategy-level error is consistently larger than the underlying
    equity-level error it's built from, sometimes far larger** (85-combo
    B=8: a 0.06 equity MAE turns into a 0.42 mean strategy TV and a
    0.99 *max* — a real hand's strategy can end up almost entirely
    unlike its true equilibrium) — the precisely-bounded heterogeneous-
    bucket risk flagged at design time (a blended bucket strategy isn't
    any individual member's real best response) is confirmed to
    materialize in practice, not just remain a theoretical risk, and it
    compounds rather than dampens as bucket coarseness increases
    relative to pool size.
  - **Conclusion, reported honestly rather than pushed further into the
    roadmap unexamined:** at the scales measured so far, card
    abstraction wired into a single-street CFR solve is not a viable
    real-time-speed lever — it doesn't meaningfully speed up the actual
    bottleneck (equity-table construction) and it measurably degrades
    strategy accuracy, worse than its own already-known equity-level
    error would suggest. This doesn't invalidate the 4-phase roadmap's
    later phases (canonicalization and an offline precomputed library
    both sidestep live equity-table construction entirely, which is
    where this milestone's finding says the real cost actually lives),
    but it does mean phase 1's card abstraction, as built, isn't the
    lever that makes live per-request solving fast — a real, measured
    course correction for the roadmap, not a hidden setback.
  - Engine only, no `api/main.py`/frontend changes — matches M12/M13/
    M17's own precedent for a milestone whose entire point is a
    measurement.

- **M19 — Situation canonicalization (Phase 2 of the real-time-speed
  roadmap).** `poker_solver/canonicalize.py` (new) —
  `canonicalize_board`, `translate_card`/`translate_cards`/
  `translate_combo`, `invert_suit_map`, `canonical_stack_depth`. An
  exact, lossless suit-relabeling symmetry — deliberately distinct from
  M17/M18's equity-based bucketing, which collapses *strategically
  similar but physically different* combos, lossily. Canonicalization
  here collapses *physically identical-up-to-suit-labeling*
  boards/hands, losslessly: two boards that are literal suit
  relabelings of each other represent the exact same strategic spot,
  and a result computed for one translates back to the other with zero
  information loss. Ships as a standalone primitive with zero existing
  callers — same precedent as `combos.py` (M10, wired in by M11) and
  `abstraction.py` (M17, wired in by M18) — not wired into
  `solve_flop`/`solve_flop_abstracted`/`solve_flop_turn`/
  `solve_flop_to_river`, since that's Phase 3's job (an offline
  precomputed spot library, still unscoped) once its own cache-key
  shape is known from actually building it.
  - **A real algorithm-selection finding, caught during design
    validation, not code review:** the first design sketched was a
    single-pass "walk board cards in dealt order, first new suit seen
    gets the next canonical letter" heuristic, preserving board-card
    order. Brute-force-validated against the true suit-isomorphism
    minimum (all 22,100 possible flops, compared to a 24-permutation
    search) before any code was trusted: the naive walk produced 1,911
    distinct forms against the true minimum of 1,755 — 156 real
    equivalence classes needlessly split. Root cause: a **paired rank
    on the board** makes "which same-rank card came first" an
    arbitrary, strategically meaningless input accident the naive walk
    is nonetheless sensitive to (`2c 2h 3c` and `2c 2h 3h` are
    genuinely suit-isomorphic via the c↔h swap, but the naive walk
    canonicalizes them differently; sorting the input by rank first
    doesn't fix it either — the tie-break among same-rank cards still
    leaks raw suit identity through). A second, independent gap:
    preserving board-card order would let the literal same physical
    board fail to canonicalize together if listed differently —
    directly working against this milestone's purpose of maximizing a
    future library's hit rate. Fixed by searching all 24 suit
    permutations and taking the lexicographically-smallest
    `(rank, canonical_suit)`-sorted result — the textbook-correct
    suit-automorphism-group minimum, and *simpler* than the naive
    design besides: the winning permutation is already a total
    bijection over all 4 suits, so the naive design's separate "now
    handle suits absent from the board" step disappears entirely.
    `canonicalize_board`'s docstring and a dedicated regression test
    (the `2c 2h 3c`/`2c 2h 3h` pair) both carry this finding forward.
  - **Documented, not built:** action-history-shape canonicalization
    needs no new code — `game_tree.StreetConfig`'s fixed
    `raise_sizes`/`max_raises` menu already makes two situations solved
    under the same bet-sizing menu directly comparable. Recorded as an
    existing invariant Phase 2 relies on, in `canonicalize.py`'s module
    docstring.
  - `translate_combo` is a thin wrapper, not a reimplementation:
    `HandCombo.__post_init__` (M10) already order-normalizes its two
    cards by `(value, suit)`, so reconstructing a `HandCombo` from
    translated cards re-runs that normalization for free — no separate
    hole-card rank-ordering logic needed.
  - `canonical_stack_depth` buckets to the nearest multiple of
    `DEFAULT_STACK_BUCKET_BB = 5.0` bb via Python's `round()` — its
    round-half-to-even ("banker's rounding") behavior is documented and
    pinned by a test (`12.5 -> 10.0`, `17.5 -> 20.0`, rounding in
    *opposite* directions at the same bucket size), not silently relied
    on.
  - **Measured, not assumed:** exhaustive enumeration of all
    `C(52,3)=22,100` flops finds exactly **1,755** distinct canonical
    forms (~1s, asserted as a permanent regression test); all
    `C(52,4)=270,725` turns find exactly **16,432** (~7-28s depending on
    machine load, also asserted as a permanent test — both figures
    matched this milestone's own design-time prediction exactly).
    River (`C(52,5)=2,598,960`) was measured once, not asserted as a
    permanent test (too slow to pay on every future full-suite rerun
    for one number, the same cost-honesty precedent M9/M12/M13/M17/M18
    already established): **134,459** distinct canonical forms, in
    342.9s — well past the ~70s linear extrapolation from the turn
    measurement's per-board cost. Traced, not left unexplained: that
    measurement ran concurrently with a full `pytest tests/` background
    run on the same machine, the same kind of concurrent-load
    distortion M14 already documented for its own pre-warm-window
    measurement — the ~343s figure reflects real contention, not
    evidence the algorithm itself scales worse than linearly; not
    re-measured in isolation since the milestone's actual deliverable
    (the flop/turn regression tests) doesn't depend on the river
    figure's precision.
  - Engine only, no `api/main.py`/frontend changes, zero changes to any
    existing module — matches M10/M17's own precedent for a
    zero-existing-callers primitive milestone.

- **M20 — Offline precomputed spot library (Phase 3 of the
  real-time-speed roadmap).** `poker_solver/library.py` (new) — the
  first real consumer of M19's `canonicalize.py`. `LibraryEntry`
  (frozen dataclass: `canonical_board`, `canonical_stack_bb`, `pot`,
  `strategy`, `iterations`, `elapsed_seconds`) + `build_library`/
  `save_library`/`load_library`/`lookup_strategy`. Flop-only,
  opening-node-only (a library entry answers "what should first-to-act
  do on this canonical flop, at this stack depth" — not facing-a-bet or
  any deeper node; the solve already produces that data cheaply via
  `node_data`, but which node(s) to store is really "which action paths
  does this library serve," the same question M16's `derive_ranges_
  from_path` already generalizes, a natural, cheap follow-on left for
  whenever a real consumer needs it, not attempted here). Canonical key
  deliberately omits `pot` — one `build_library` call fixes `pot` for
  every entry it builds, the same "fixed menu" reasoning `canonicalize.
  py`'s own docstring already applies to `raise_sizes`/`max_raises`; a
  multi-pot/SPR-indexed library is a flagged, explicit out-of-scope
  follow-on.
  - **The crux design question, resolved and proven, not hand-waved:**
    `build_library` only accepts *class*-frequency dicts (`StartingHand
    -> weight`), never raw per-combo `HandCombo` dicts — the only way a
    canonical hit correctly serves *any* real board isomorphic to a
    stored entry, not just the literal board a solve happened to run
    against. For two real boards `B1`/`B2` that canonicalize to the
    same `C` via generally *different* winning suit permutations to
    safely share one entry, the translated (combo -> weight) dict must
    come out identical either way — true because `combos.range_from_
    class_frequencies` is suit-blind by construction (`combos_for_
    class` enumerates from `StartingHand.high_rank`/`.low_rank` alone,
    never a fixed suit, confirmed by reading the code, not assumed), so
    applying any full 4-suit bijection just relabels suits throughout
    without changing which combos exist or how many, and the uniform
    per-class weighting makes the equivalence exact, not approximate.
    This fails for a hand-picked, suit-asymmetric range (e.g. just
    `{"AcKc": 1.0}`) — translating one combo under two different
    winning permutations generally produces two different canonical
    combos, silently misrepresenting a query built from a different
    permutation. No cheap general runtime check catches this, so the
    design forecloses the footgun at the API boundary instead:
    class-dicts-only, which are suit-symmetric by construction. Stated
    as an explicit limitation: a caller needing an arbitrary/asymmetric
    combo-level range is out of scope for this primitive (M16's
    `derive_ranges_from_path` territory, feeding into Phase 4).
  - Solves at the *canonical* (bucketed) stack depth via M19's
    `canonical_stack_depth`, never the raw input depth, so every entry
    sharing a canonical key is genuinely interchangeable — verified by
    a dedicated test that cross-checks a stored entry against a direct
    `solve_flop` call at the bucketed depth, not the raw one.
  - `save_library`/`load_library` use JSON (an entry-list shape, not a
    packed string dict key — self-describing, hand-inspectable),
    mirroring `equity.py`'s `cache_path`/`path.parent.mkdir` on-disk-
    cache style precedent (the first non-numpy on-disk cache in the
    repo).
  - **Tested end to end, not just structurally:** a library built by
    solving real board `A` only is queried with a real board `B` — a
    genuine suit relabeling of `A`, physically different, never solved
    directly — and the returned strategy is confirmed to match a fresh
    direct `solve_flop(B, ...)` call *exactly* (not approximate-with-
    slack; suit relabeling preserves all hand-strength/showdown
    outcomes exactly). This is the concrete proof of the crux design
    answer above, not just an assertion of it.
  - **Measured, not assumed:** a 20-canonical-board library (verified
    pairwise suit-non-isomorphic before building, so the count isn't
    silently inflated by an accidental duplicate), at the same 23-combo
    demo scale M17/M18 used (2 hero classes / 2 villain classes),
    built in **18.47s total, ~0.92s/board** — in the same ballpark as
    M18's own single-solve figure at this scale (~1.21s), the small
    difference explained by this measurement's smaller tree (`max_
    raises=2`, one raise size) rather than any change to the underlying
    solve cost. Not the full 1,755-board production library (M19's own
    measured flop-canonicalization count) — a later, separate concern
    once this primitive's shape is validated to work at all, same
    "prove it small first" discipline every primitive milestone this
    session has followed.
  - Engine only, no `api/main.py`/frontend changes, zero changes to any
    existing module, `library.py` not added to `poker_solver/__init__.
    py`'s `__all__` — matches M17/M19's own standalone-primitive
    precedent.

- **M21 — Live query path (Phase 4 of the real-time-speed roadmap,
  closing it).** `poker_solver/library.py` gained `QueryResult` (frozen
  dataclass: `strategy`, `hit`, `elapsed_seconds`) + `query_strategy`:
  canonicalize-then-lookup, falling back to an on-demand solve (via
  `build_library`, not duplicated logic) on a miss, caching the result
  into the caller's own `library` dict *in place* so a subsequent hit —
  on the same board *or* any isomorphic one — really is instant.
  - The post-insert re-lookup is provably a hit, not just expected to
    be: `canonicalize_board`/`canonical_stack_depth` are pure functions
    of their inputs alone, and `query_strategy` threads the identical
    `board`/`effective_stack_bb`/`stack_bucket_bb` into the triggering
    lookup, the `build_library` call, and the final lookup — enforced
    with an explicit `RuntimeError` (not a bare `assert`, which `python
    -O` would silently strip, and which has no precedent elsewhere in
    `poker_solver/`).
  - **A real, previously-undiscovered subtlety, caught by writing a
    more rigorous test than M20 shipped, not by code review:** M20's
    own "isomorphic board hit" test (and this milestone's first draft
    of an analogous test) asserted an *exact* match between a
    translated canonical-space strategy and a fresh, independently
    re-seeded direct solve of a different real board — and it happened
    to pass only because that test's second board was, unnoticed,
    *literally* the first board's own canonical form (an identity
    suit-map, not a real relabeling). Written against a genuinely
    different (non-canonical) real board instead, the same comparison
    fails — not from a translation bug (`invert_suit_map`/
    `translate_combo` correctly reassign each value to its right
    combo, confirmed by hand-tracing the exact permutation), but
    because flop-level equity (`remaining_needed=2`) is Monte Carlo
    sampled, and `cards.remaining_deck`'s rank-then-suit iteration
    order differs between two differently-suited (even if isomorphic)
    boards — so the same `equity_seed` draws genuinely different
    specific runouts for each, a real, small, finite-sample
    discrepancy, not a bug. **What's still exactly true, and tested as
    such:** the deterministic translate-in/solve/translate-out pipeline
    is bit-reproducible against *itself* (confirmed via a corrected
    test that compares the stored entry against a fresh solve of the
    *same* canonical board, sidestepping the cross-board sampling-order
    issue entirely — the same pattern M20's own bucketed-stack-depth
    test already used, just not yet applied to this comparison). **What
    is not exactly true, now stated rather than silently assumed:** a
    canonical hit's translated result is the correct translation of
    whatever the *original* canonical solve computed, not necessarily
    bit-identical to what an independent fresh Monte-Carlo solve of
    that *specific* isomorphic real board would separately produce.
    This doesn't weaken M20's actual crux property (a hit correctly
    *serves* any isomorphic board without re-solving) — it refines what
    "correctly" was ever precisely shown to mean, the same kind of
    honest correction this project applied to M15's wrong test
    assumption and M18's dual-reach subtlety.
  - **A related, smaller gotcha, named explicitly and pinned by a
    test:** `pot`/`positions`/`raise_sizes`/`max_raises` are not part
    of the canonical key (inherited from `build_library`/`lookup_
    strategy`'s own "fixed menu" cut) — a second `query_strategy` call
    against an already-cached (board, stack) with a *different* `pot`
    still hits and silently returns the *first* call's strategy; it
    does not re-solve, does not raise. Callers sharing one `library`
    across genuinely different pots/sizing menus need their own
    external key discipline.
  - Known, deliberate limitations, stated rather than glossed over: no
    automatic `save_library` persistence on a miss (in-memory only); no
    concurrency control (two simultaneous callers hitting the same miss
    could both solve and both write — correct final state, wasted
    duplicate work; a real live server layer would need its own
    serialization strategy, not attempted here); no connection yet to
    `derive_ranges_from_path` (M16) — mostly a direct fit
    (`StartingHand`-keyed output already matches `query_strategy`'s own
    input shape), but `PathScenario.stacks` is a per-position dict, not
    the single `effective_stack_bb` float this function expects, so an
    arbitrary path needs an explicit "both live positions' remaining
    stacks are equal here" check before that hookup would be safe.
  - **Measured, not assumed — the concluding number this whole 4-phase
    roadmap has been chasing since M17:** at the same ~23-combo demo
    scale M18/M20 used (confirmed via `len()`, not assumed), 5 distinct
    real boards' miss timings averaged **0.951s** (range 0.717s-1.019s,
    consistent with M20's own ~0.92s/board figure — a miss *is* a
    one-board `build_library` call). 1000 repeated hits on one warmed
    board averaged **0.1507ms** (min 0.1465ms) — genuinely no CFR, no
    equity-table construction, just a canonicalize + dict `.get()` +
    a handful of `translate_combo` calls. A hit against a *different*,
    merely-isomorphic board (never itself solved) cost the same
    (~0.152ms), confirming hit cost doesn't depend on literal-vs-
    isomorphic match. **Ratio: ~6,313x.** This is the real, measured
    answer to the question the roadmap exists to answer — not the
    speedup M17's card abstraction alone failed to deliver (M18), but
    the one avoiding live equity-table construction entirely on a hit
    (M19+M20+M21 together) actually does.
  - Engine only: no `api/main.py`/frontend wiring (a live endpoint
    calling `query_strategy` against a real, persistent, shared
    library — including a concurrent-miss serialization decision this
    milestone didn't need to make), and no `derive_ranges_from_path`
    connection — both natural next milestones, mirroring M12/M13-
    before-M14's two-primitives-then-one-wiring-milestone pattern.

- **M22 — Wire `query_strategy` into a live endpoint + frontend,**
  closing the first of M21's two explicitly-deferred follow-ons (the
  second, a `derive_ranges_from_path` connection, remains open). `GET
  /solve_flop_cached` — board + stack in, hero's per-combo strategy out,
  same shape as `/solve_flop`, backed by `poker_solver.library.
  query_strategy` (M21) instead of a plain per-request cache dict.
  - **The one load-bearing design principle:** only `board`/`stack_bb`
    are query params — everything else `query_strategy` takes (`pot`,
    `positions`, `raise_sizes`, `max_raises`, the demo classes,
    `iterations`) is a fixed server constant. Reasoning: `query_
    strategy`'s canonical cache key is *only* `(canonical_board,
    canonical_stack_bb)` — those are the only two inputs that actually
    feed it. Every other parameter is silently ignored on a cache hit
    (generalizes M21's own "pot-not-in-key gotcha" to *every* non-key
    parameter) — exposing any of them as a free-varying query param on
    a *persistent, cross-request* library would let a later request's
    stated value silently not match what was actually solved, echoed
    back in the response as if it had been honored. Fixing everything
    except the two real key-contributing params avoids that whole bug
    class by construction. No `position` param either — confirmed
    structurally, not assumed: `StrategyResult.opening_range()` is
    `strategy_at(self.root)`, and `LibraryEntry.strategy` is that
    verbatim — there is no second position's data anywhere in this
    pipeline for a request to select, unlike the other three endpoints'
    full `StrategyResult` (both positions' nodes reachable). The
    response always reports `"OOP"`.
  - New `FlopQueryResponse` (`api/schemas.py`) adds `canonical_board`/
    `canonical_stack_bb` — transparency fields no other response type
    has, showing what a real board/stack actually canonicalizes to
    (the concrete thing this endpoint exists to demonstrate), and
    incidentally what makes the isomorphic-hit API test cheap (compare
    `canonical_board` directly between two responses, rather than
    re-deriving `test_library.py`'s own engine-level cross-check at
    the wrong layer).
  - **A deliberate, stricter locking departure from every other
    endpoint:** `_query_flop`'s helper holds `_flop_query_lock` for
    `query_strategy`'s *entire* call, not just around a dict read/write
    the way every `_get_or_solve_X` helper does (those only guard the
    cache dict, letting two concurrent misses for the same never-
    before-seen key both solve independently — an existing, accepted,
    previously-undocumented-as-such tradeoff every prior `/solve_flop*`
    endpoint already has). `query_strategy` is an atomic check-then-
    maybe-solve-then-insert primitive with no concurrency control of
    its own (its own docstring's "Known, deliberate limitations"); it
    can't be decomposed into a separate check step and solve step
    without reimplementing its internals in `api/main.py`. Holding one
    lock for the whole call trades a little cross-request parallelism
    (two *unrelated* concurrent misses now queue rather than solve in
    parallel) for a *stronger* guarantee than any existing endpoint has
    — no concurrent-miss double-solve at all — directly resolving
    `query_strategy`'s own documented gap for this one live surface.
  - **Deliberately not pre-warmed**, unlike `/solve_flop_turn`/
    `/solve_flop_to_river` — pre-warming the frontend's own default
    board would mean a user's very first, unmodified click already
    showed a cache hit, undercutting the one thing this endpoint exists
    to demonstrate (a user should get to witness a miss at least once).
  - **A real behavioral difference from the other three endpoints,
    documented not just implemented:** `QueryResult.elapsed_seconds` is
    measured fresh on *every* call, hit or miss — unlike the other
    three endpoints' cached responses, where it's frozen at original-
    solve time (`test_solve_flop_is_cached_across_positions`'s own
    exact-equality assertion proves that). Repeated hits here show a
    small, non-identical `elapsed_seconds` each time — the literal
    "hit is fast" proof this endpoint exists to show, not a bug.
  - **Measured, not assumed, at this endpoint's own fresh pool** (2
    hero classes / 2 villain classes, confirmed via `len()` at **23
    combos** — the same scale M17/M18/M20/M21 all used, not re-cited
    from an old uncommitted figure): through the real `TestClient`-
    exercised endpoint (full FastAPI/Starlette stack, not just the bare
    engine call M21 measured), 4 distinct pairwise-non-isomorphic
    boards' miss timings averaged **1.551s** (range 1.260s-1.793s,
    somewhat higher than M21's own bare-engine ~0.95s — the FastAPI
    request/response/JSON-serialization overhead on top of the same
    underlying solve, a real and expected difference, not a
    regression). 200 repeated hits on one warmed board averaged
    **0.1998ms** (min 0.1721ms). A hit against a different, merely-
    isomorphic board (`2c7d9h`, never itself queried) cost the same
    (~0.19ms) and reported the identical `canonical_board`. **Ratio:
    ~7,763x.**
  - **Frontend:** `CachedFlopSolver.tsx` (new, a sibling to
    `FlopSolver`, not a 4th runout depth bolted onto its selector —
    the response shape and interaction differ enough that shoehorning
    it in would need messy conditional branching, matching the
    established "different interaction shape -> separate component"
    precedent `EquityCalculator`/`FlopSolver` themselves set). A
    "Shuffle suits" button rotates the board's suits by a fixed
    permutation (`c->d->h->s->c`, one specific element of the same
    24-permutation group `canonicalize_board` searches, so guaranteed
    to preserve canonical form and safe under repeated clicks) —
    without it, clicking Solve twice on identical unmodified text only
    proves ordinary memoization, indistinguishable from what every
    other `/solve_flop*` endpoint's own cache already does; shuffling
    first makes the actual point (a hit on a board *never itself
    queried*) visible. Pulled into its own `frontend/src/boardSuits.ts`
    module (not colocated in the component) to keep `CachedFlopSolver.
    tsx` exporting only its component, matching every other component
    file's own convention — `shuffleSuits`'s own composition property
    (4 applications of the rotation return to the identity) is directly
    tested there. A new `.hit-indicator` pill (green for a hit, neutral
    gray for a miss — not red, since a miss is a valid, expected
    first-query outcome, not an error) is the one genuinely new UI
    element; everything else reuses `FlopSolver`'s existing CSS classes
    wholesale. Live-verified in the browser end to end: a fresh board
    shows "Solved live" at ~1.5s; clicking Solve again on the identical
    board shows "Cache hit" at ~0.2ms; shuffling suits then solving on
    the *shuffled* text (never itself queried) also shows "Cache hit"
    at ~0.2ms with the identical `canonical_board` — the real,
    on-screen proof of the whole roadmap's payoff; a malformed board
    correctly produces the existing error-banner path and clears any
    stale result.

- **M23 — Connect `derive_ranges_from_path` to `query_strategy`
  (engine only).** Closes the second of M21's two explicitly-deferred
  follow-ons (the first, a live `query_strategy` endpoint, shipped in
  M22 — against a fixed demo range, not a real user-described
  situation). New `poker_solver/library.py` function `query_strategy_
  from_path(library, result, path_scenario, board, ...)`: given a
  preflop `StrategyResult` and the `PathScenario` `derive_ranges_from_
  path` (M16) derived from walking a real `action_path` against it,
  derives `query_strategy`'s `hero_classes`/`villain_classes`/`pot`/
  `effective_stack_bb`/`positions` inputs and calls it — reusing
  `query_strategy` outright, not reimplementing its canonicalize/
  cache/solve logic.
  - **The real technical finding:** `query_strategy`'s own (M21)
    docstring framed the blocker as needing a stacks-equality check.
    The correct, provably sufficient check is not a stacks comparison
    at all — it's `isinstance(path_scenario.node, TerminalNode)`.
    Stack equality alone is insufficient (a limped pot leaves both
    stacks equal while the big blind still has a live decision
    pending — check-back closes the round, raise reopens it);
    `TerminalNode`-ness is both necessary and sufficient, proved
    directly from `game_tree._build`'s no-side-pots construction
    (every `CALL_OR_CHECK` matches `current_bet` exactly, every
    `ALL_IN` commits exactly `config.stack_bb`, so `to_act` can only
    empty with 2+ live players once all of them match the same final
    bet level — including the all-in case, where no side pots at any N
    means two live players can never end up all-in at different
    amounts). Combined with `derive_ranges_from_path`'s own already-
    enforced "fewer than 2 live positions raises `ValueError`," a
    returned `PathScenario` whose `node` is a `TerminalNode` is
    automatically guaranteed 2+ live positions with exactly equal
    remaining stacks — a proven consequence, cross-checked with an
    explicit `RuntimeError` (not a bare `assert` — the same `python -O`
    -safety precedent `query_strategy`'s own post-insert-lookup check
    already set, the only other invariant check like it in
    `poker_solver/`), not re-derived as a second independent condition.
  - **A second, independent guard: `result` must be preflop-rooted**
    (`isinstance(result.config, GameConfig)`), not just heads-up.
    `StrategyResult`/`PathScenario`/`derive_ranges_from_path` are
    deliberately street-agnostic — a `PathScenario` from a `solve_flop`
    result can equally reach a 2-live-position `TerminalNode`, but its
    `ranges` are `HandCombo`-keyed, not `StartingHand`-keyed, silently
    violating `query_strategy`'s documented class-dict-only contract
    (confirmed directly: `HandCombo` has no `.high_rank`/`.low_rank`,
    which `combos_for_class` immediately reads off its input's keys —
    without this guard, the mistake would surface as a confusing
    `AttributeError` several frames deep, not a clear error here).
  - **Heads-up-origin only** (`len(result.config.positions) == 2`) —
    the same multiway cut `derive_flop_scenario` (M15) already made,
    for the same reason: mapping a surviving subset of a 3+-handed
    table to postflop OOP/IP depends on the full original seating
    order. Postflop solving in this project (M11-M22) is heads-up-only
    regardless, so this costs nothing real today.
  - **A backwards test convention, corrected:** real heads-up poker's
    button acts first preflop but last (in position, IP) every street
    after; the big blind acts last preflop but first (OOP) postflop.
    `result.config.positions[0]` -> postflop `"IP"`; `positions[1]` ->
    `"OOP"`. Two existing M15/M16 pipeline tests bind the preflop
    raiser (BTN in both) to `hero_range`/`positions[0]`/`"OOP"` — the
    reverse of real mechanics, incidental test convenience rather than
    a deliberate convention. Pinned by a new regression test that
    cross-checks `query_strategy_from_path`'s result against a direct
    `query_strategy` call using the correct explicit mapping.
  - Engine only — no `api/main.py`/frontend wiring (a live endpoint
    accepting a real, untrusted action-path description is a
    materially bigger, separate API-design question) — matches
    M15/M16/M19/M20/M21's own bridge-milestone precedent. Confirmed M22
    did not already partially cover this: `/solve_flop_cached` calls
    `query_strategy` with fixed constants, never anything path-derived.

- **M24 — Live endpoint (+ frontend) for a real action-path
  description.** Closes the last thing M21/M22/M23 each still listed
  as remaining. `POST /solve_flop_from_path` — stack in (big blinds),
  a sequence of action *kinds* (`"raise"`/`"call_or_check"`/`"fold"`/
  `"all_in"`, no exact sizes needed), a flop board, out comes hero's
  per-combo strategy on a real, user-described situation rather than a
  fixed demo range. The first `POST`/request-body route in this app
  (every other route is `GET`-with-query-params — a variable-length
  structured action sequence doesn't fit that shape naturally).
  `poker_solver/game_tree.py` gains `resolve_action(node, kind)` — the
  first *shared* version of a pattern ~30 call sites across this
  codebase had each inlined separately (`next(a for a in node.
  legal_actions if a.kind == X)`), safe because at most one sized
  `RAISE` action can ever exist at a single node (confirmed structural,
  not incidental — `_raise_total_size` computes one scalar, assigned
  once inside one `if`, never a loop), so a bare kind is never
  ambiguous.
  - **Finding 1, measured before shipping, not assumed: a fully general
    backend would have been catastrophic, not just slow.**
    `derive_ranges_from_path` doesn't prune anything — a real walked
    path against the actual 169-class preflop pool left **both sides'
    entire pool nonzero** (CFR+'s own floating-point floor, not a
    meaningful signal), a **~1,176-combo union** that would cost on the
    order of *hours per request* fed straight into `solve_flop`'s O(N²)
    equity-table build. Fixed at the API layer, not the engine —
    `derive_ranges_from_path`/`query_strategy_from_path` are untouched,
    exactly as general as M16/M23 built them — by capping the *derived*
    range to the top `MAX_PATH_QUERY_CLASSES_PER_SIDE` (6)
    highest-frequency classes per side (via `dataclasses.replace` on
    `PathScenario`'s frozen `ranges` field) before calling `query_
    strategy_from_path`. Measured for real: ~82 combos, ~21s/miss on a
    real 3-step path — the same "shipped anyway, real measured cost,
    not hidden" bucket M9/M14 already established. Also confirmed the
    cap keeps the *realistic* range, not an arbitrary one: on a real
    3-bet-call line, the top-ranked BTN classes were JTs/QTs/Q9s/A6s/
    A7o, with AA/KK/QQ/AKs at the *bottom* — the same "premium hands
    4-bet/jam rather than flat a 3-bet" pattern M16 already documented,
    resurfacing here as independent confirmation top-K keeps signal,
    not noise.
  - **Finding 2, also caught before shipping: sharing one library dict
    (M22's own pattern) would have silently corrupted answers here.**
    `query_strategy`'s canonical key is `(canonical_board, canonical_
    stack_bb)` only — safe for `/solve_flop_cached` because its range/
    pot are fixed constants, identical on every call. This endpoint's
    range/pot are derived fresh per request from each client's own
    `action_path` — two unrelated real situations that happen to
    canonicalize to the same (board, stack-bucket) key would silently
    return each other's answer on a "hit." Fixed with a partitioned
    `_path_query_libraries`, one private library per distinct
    `(action_path, stack_bb, iterations)`, never one shared dict —
    verified live, not just reasoned about: two different action paths
    at the identical board/stack produced two separate partitions,
    correctly different pots, and correctly different strategies.
  - `_get_or_solve_preflop_raw` caches the *raw* `StrategyResult`
    (unlike `/solve/{stack_bb}`'s own `_get_or_solve`, which formats
    and discards it) — mirrors `_get_or_solve_multiway`'s own
    "cache the raw result" precedent, needed here since `derive_
    ranges_from_path` has to walk the real tree/`node_data`.
  - Two independent, deliberately different iteration decisions: the
    *preflop*-stage `iterations` is a real request field (capped by the
    existing `MAX_ITERATIONS`) — safe, since `_get_or_solve_preflop_raw`
    is a plain per-request cache dict, exactly like `/solve/{stack_bb}`
    already relies on. The *flop*-stage iterations stay a fixed
    constant, not exposed — that part sits behind `query_strategy`'s
    canonical-library abstraction, where a client-varying value would
    be silently ignored on a hit, the exact bug class `/solve_flop_
    cached`'s own design principle already guards against.
  - **Frontend:** `ActionPathSolver.tsx` (new) — a curated 3-preset
    selector ("BTN opens, BB calls" / "BTN opens, BB 3-bets, BTN
    calls" / "BTN limps, BB checks back"), deliberately not a general
    step-by-step action-path builder. A true general wizard needs its
    own companion "what's legal from here" endpoint plus incremental
    round-trip state management — real, separate scope this project
    has never built in the same milestone as the general backend (M9's
    curated hand subset, M11's curated range before M16 generalized
    the *input* side, M14's curated pool). The backend stays fully
    general regardless (costs nothing extra beyond the cap above), so
    a future milestone can build the general wizard without touching
    the route/schema again. `api.ts`'s `fetchJson` generalized from a
    bare `signal?` param to a full `RequestInit` for this endpoint's
    `POST`/JSON body — the first of its kind — confirmed backward-
    compatible with every existing `GET` caller (same `fetch(url, {
    signal })` call shape either way, no existing test assertions
    needed to change). Live-verified in the browser end to end: each
    preset solves and renders a real strategy with sane `pot`/
    `effective_stack_bb` numbers (the 3-bet preset's pot visibly
    exceeds the open-call preset's); re-solving the identical preset/
    board/stack shows a cache hit with a sharp `elapsed_seconds` drop;
    a malformed board produces the existing error-banner path and
    clears correctly; switching presets clears a stale result.

- **M25 — General step-by-step action-path builder.** Closes the exact
  gap M24's own docstring and CLAUDE.md's v3 vision both flagged: a
  general wizard needs its own companion "what's legal from here"
  endpoint plus incremental round-trip state management. `POST
  /preflop_walk` — stack + an `action_path` so far in, the node it
  resolves to's legal actions (plus `pot`/`player_to_act`/
  `live_positions`/`is_terminal`) out. Board-independent and boardless —
  a pure preflop-tree-state query, no CFR strategy, no `query_strategy`
  at all — so none of M24's range-capping or partitioned-library
  machinery applies here; it reuses `_get_or_solve_preflop_raw`'s
  existing cache unmodified (confirmed live, not just reasoned: walking
  a path then solving it via `/solve_flop_from_path` left exactly one
  entry in `_preflop_raw_cache`).
  - `_resolve_action_path` (M24) now returns `(actions, node)` instead
    of just the action list — its one existing call site (inside
    `_query_flop_from_path`) discards the node unchanged, and the new
    `_preflop_walk` helper uses it directly. Considered and rejected:
    routing through `solver.PathScenario.node` (via `derive_ranges_
    from_path`) instead — `derive_ranges_from_path` raises on fewer
    than 2 live positions, so every fold-out path would crash instead
    of returning a clean terminal response, and it would pay for
    per-step reach-multiplication a query that never uses ranges
    doesn't need.
  - **The "amount to call" safety argument, proven, not assumed:**
    `_preflop_walk` computes `to_call` from `max(node.invested.
    values())` over *every* position, folded ones included — safe only
    because `game_tree._build` never offers FOLD unless `to_call > 0`
    at that instant, so the position actually holding the current max
    can never fold; every other action only ever raises the acting
    position's invested to `>=` the pre-action max. So the true live
    max is monotonically non-decreasing along any path, and a folded
    position's frozen `invested` can never exceed a later node's true
    max — the all-positions max always equals the live-only max
    `_build` itself would compute.
  - A terminal node is not automatically postflop-eligible: a fold-out
    is terminal too, with only 1 live position, which `/solve_flop_
    from_path` 422s on. `_preflop_walk` reports `live_positions`
    explicitly rather than leaving the caller to infer it from
    `is_terminal` alone — load-bearing for the frontend's own
    real-terminal-vs-fold-out branch below.
  - `frontend/vite.config.ts` needed its own new `/preflop_walk` proxy
    entry — the existing list only prefix-matches `/solve`/`/equity`,
    and every `POST` route added since M14 happened to be named `/solve_
    something`, so this genuinely new prefix would otherwise silently
    fall through to the SPA's `index.html`, the exact class of bug M10
    hit for real with `/equity` before that entry existed.
  - **Frontend:** `usePreflopWalk.ts` (new) — mirrors `useOpeningRange.
    ts`'s effect+abort+primitive-flattening shape (`actionPath.join('|')`
    standing in for `useOpeningRange`'s own params-destructuring), with
    `SolveError`-aware error handling to match `ActionPathSolver.tsx`'s
    existing convention instead. `ActionPathSolver.tsx` reworked in
    place: `action_path` now grows one legal click at a time (`walk =
    usePreflopWalk(stackBb, actionPath)`), with Undo/Reset controls and
    the 3 presets kept on as one-click shortcuts into the same growing
    path rather than the only paths reachable. The board/Solve UI
    (unchanged, still M24's `fetchFlopStrategyFromPath`) is gated on
    `is_terminal && live_positions.length >= 2`, with a distinct
    "hand's over, `<position>` wins the pot" message when a fold
    leaves only 1 live position — the real correctness gap the terminal/
    live_positions distinction above exists to prevent. A stack change
    resets `action_path` to `[]` directly in the input handler, not a
    separate effect. Live-verified in the browser end to end: the root
    walk (BTN to act, pot 1.5bb, all 4 actions with the right
    `to_call`/sizes); clicking Raise advances to BB's turn with the
    correct 1.5bb `to_call`; Undo reverts exactly one step; the
    open-call preset reaches a real terminal, solves, and renders a
    sane strategy (~9.2s, a live miss); Reset clears back to root; a
    Fold at root shows "Hand's over — BB wins the 1.5bb pot." with no
    board input; changing the stack (100 → 50) resets the path *and*
    correctly re-derives the new all-in size (100 → 50); an
    intentionally-too-small stack (0.2bb) surfaces a walk-error banner
    with no legal-action buttons; and, separately, a malformed board on
    a real terminal surfaces a solve-error banner while leaving the
    walk state (board input, Solve button, breadcrumb) fully intact —
    a solve-step failure never resets the wizard.

- **M26 — Live turn-level advice (`POST /solve_turn_from_path`).**
  Closes the gap M14's own module docstring had named, unchanged, since
  it shipped: `/solve_flop_turn`/`/solve_flop_to_river` already run one
  CFR solve over the *entire* joint flop+turn(+river) tree, and their
  result's `chance_data` dict already holds a real, fully-solved turn/
  river `DecisionNode` for every dealt-card branch — nothing before
  this milestone ever walked in and read one out ("an interactive
  turn/river explorer is a separate, materially bigger feature, not
  attempted"). It turned out to already be a plumbing gap, not a
  solving one: **measured, not assumed**, walking into one specific
  branch and calling `strategy_at()` on it costs ~0.04ms *after* the
  solve that produces it — reading `chance_data` is free. Zero new
  engine code was needed in `poker_solver/` for this milestone; it
  chains three existing pieces (`_get_or_solve_preflop_raw` +
  `_resolve_action_path` + `derive_ranges_from_path` for the preflop
  leg, `solve_flop_turn` for the flop+turn solve, `_resolve_action_path`
  again — reused unchanged a second time in one request, now walking
  the flop_turn result's own root — to find which chance-eligible
  terminal the client's `flop_action_path` reaches).
  - **A real bug a design-validation pass caught before any code
    shipped, not smoothed over:** a first draft silently dropped a
    safety check that only existed inside `library.query_strategy_
    from_path` — bypassed here because its canonical-library machinery
    doesn't fit a per-turn-card query shape, but its two checks
    (`path_scenario.node` must be a `TerminalNode`; both live
    positions' remaining stacks must be equal) are *not* specific to
    that machinery and are still required. Without them, an unclosed
    preflop path (e.g. BB hasn't acted after BTN's raise) would
    silently feed one side's stack into `solve_flop_turn` as if both
    players shared it, instead of erroring cleanly. Ported explicitly;
    regression-tested (`test_solve_turn_from_path_rejects_a_non_
    terminal_preflop_path`, mirroring the equivalent existing test for
    `/solve_flop_from_path`).
  - **A second real bug the same pass caught:** the original cache-key
    design included `flop_action_path`/`turn_card` — which would have
    forced a full re-solve for every different *turn card* asked about
    against an *identical* preflop+flop situation, directly defeating
    the "reading chance_data afterward is free" finding above. Fixed
    by keying `_turn_path_cache` narrowly — only what `solve_flop_
    turn`'s own cost actually depends on (the preflop leg, the board,
    both iteration counts) — and resolving `flop_action_path`/
    `turn_card` by walking the already-solved tree on every call
    instead, since that's already free. Regression-tested: two
    requests differing only in `turn_card` (or only in
    `flop_action_path`) leave exactly one entry in `_turn_path_cache`,
    while two requests with genuinely different preflop legs leave two
    — proving the key is neither too coarse nor too narrow.
  - **A third finding, caught by testing the real pipeline instead of
    an isolated call, and the most consequential of the three:** an
    early version reused `MAX_PATH_QUERY_CLASSES_PER_SIDE` (6, already
    tuned for `/solve_flop_from_path`'s own `solve_flop`-via-`query_
    strategy` cost profile) directly for this endpoint's own range cap.
    A real end-to-end request measured **454s**. Root cause:
    `solve_flop_turn`'s cost curve is fundamentally steeper than
    `solve_flop`'s — it builds ~49 branch equity tables per chance-
    eligible flop terminal, not one table total — so the same 6-class
    cap (which expands to a *combo* count depending on how many of
    those classes are pairs/suited/offsuit, not a fixed number)
    produced a 58-combo pool against a real derived range, not the
    ~12-combo pool a hand-picked demo range used during planning.
    Fixed with `MAX_TURN_PATH_QUERY_CLASSES_PER_SIDE`, this endpoint's
    own, separately-measured cap. Real numbers, same preflop line/
    board/iterations throughout: cap=6 → 58 combos, 454s; cap=3 → 27
    combos, 99.9s; cap=2 → 19 combos, 45.9s; cap=1 → 7 combos, 10.2s.
    Set to 2 — landing this endpoint's real cost in the same
    already-accepted "slow but tolerable for a live, if not snappy,
    request" bracket `/solve_flop_to_river` established at M14
    (~63-105s), not the ~18-26s bracket the docstring originally,
    incorrectly, expected to carry over from an isolated measurement.
  - `iterations` (preflop leg) and `turn_iterations` (the `solve_flop_
    turn` leg) are two independent request fields, not one shared
    value — deliberately: `/solve_flop_from_path`'s own flop-stage
    iterations are fixed/unexposed specifically because that leg sits
    behind `query_strategy`'s canonical-library abstraction (a
    client-varying value would be silently ignored on a hit); this
    endpoint doesn't use that abstraction at all, so nothing forces the
    two legs to share one cap, and doing so anyway would have silently
    under-capped preflop convergence 10x below every sibling endpoint's
    own `MAX_ITERATIONS` for a reason that has nothing to do with
    preflop's own (much cheaper) cost.
  - Only the *first* turn decision is ever exposed (`branch.root`
    itself, never a deeper turn-street path) — a deliberate scope cut
    mirroring `query_strategy`'s own opening-node-only precedent, not
    an oversight. River-level advice one street further, and an
    interactive "what's legal on the flop from here" walker (this
    milestone's flop-line input is a curated preset dropdown, not a
    general wizard — mirroring `ActionPathSolver.tsx`'s own M24-before-
    M25 history), are the natural next milestones this one deliberately
    defers — both already de-risked cost-wise by this milestone's own
    measurements (a two-hop river walk, after a real `solve_flop_to_
    river` call, measured ~0.002ms), unlike every prior open question
    in this project's real-time-speed thread.
  - **Frontend:** `TurnPathSolver.tsx` (new) reuses `usePreflopWalk`
    (M25) directly for its own preflop leg — *not* a re-implementation
    of curated preflop presets. (A real correction a design-validation
    pass made before any frontend code was written: `ActionPathSolver.
    tsx` is not still M24's original curated-preset component — M25
    reworked it in place into a general interactive wizard. Building a
    new curated-only preflop selector from scratch would have been a
    real regression against what already ships.) The flop leg is 8
    curated presets — the 7 real, empirically-enumerated (not guessed)
    flop-terminal action-kind paths reachable at `FLOP_TURN_MAX_
    RAISES`/`FLOP_TURN_RAISE_SIZES`'s real values, plus one fold-out
    line to exercise that outcome. A known, stated limitation: this
    list is hardcoded to match those two server constants and would
    silently drift if they ever changed — the structural fix (enumerate
    legal flop lines live) is possible in principle (legal action
    *kinds* depend only on `StreetConfig`'s pot/stack/raise_sizes/
    max_raises, not board/range) but is the same "interactive flop
    wizard" scope jump already deferred above. Distinguishes a
    flop-line fold-out from an already-all-in-on-the-flop terminal by
    checking the *submitted* flop preset locally (does its own path end
    in `fold`?) rather than guessing from response data.

- **M27 — Fix the N-way equity fallback bias, and what full-suite
  testing caught along the way.** The first item off
  `docs/full-table-diagnostic-2026-08.md`'s prioritized recommendation
  list (SS3.1/SS3.2): `MultiwayEquityCache.traverser_equity_vector`
  (`poker_solver/equity.py`) fell back to a hardcoded `0.5` whenever a
  card combination couldn't physically be dealt — correct at 2 live
  players, wrong at N-way (0.5 is a coinflip's answer, not an n-way
  split), and reached with real, nonzero frequency by MCCFR's opponent
  sampling (which draws each opponent independently, with no
  card-removal tracking between them). A/B-confirmed on a real 9-max
  solve: UTG jammed 100bb with 72o ~50% of the time under the bug,
  ~5% once fixed. `deal_n_hands`'s exhaustive backtracking search also
  took up to 63.2 seconds (a new, worse-than-diagnostic measurement) to
  *discover* a combination was infeasible before any placeholder logic
  even ran — fixed with a new `_provably_infeasible` precheck: an O(N)
  necessary (not sufficient — a deliberately probed and documented
  boundary, with a constructed counterexample where a suit-only
  conflict still correctly falls through to the real search) pigeonhole
  check on per-rank card demand vs. supply, confirmed safe (zero false
  positives) across 23,000 weighted-random trials, cutting that same
  63.2s case to ~0.02ms.
  - **What should have been "small, localized" (the diagnostic's own
    words) was not — and the mandatory full-suite rerun is exactly what
    caught it, not a special investigation.** The straightforward fix
    (`0.5 -> 1/n_live`) passed every existing test except one:
    `test_six_max_utg_premium_hands_rarely_fold` — at 6-max, AKs opened
    by folding 94.8% of the time, KK 64%, from first position, no
    action in front. Confirmed via `git stash` that the fix was the
    direct cause. Investigated properly rather than just loosening the
    threshold, because the failure mode itself was suspicious: re-run
    at 300 / 3,000 / 30,000 iterations, AKs's fold rate was 22.8% ->
    69.2% -> 94.8% — not converging, diverging, and continuing to climb
    at 100,000 / 200,000 iterations tested later (KK: 12.4% -> 47.9% ->
    71.2%). Root cause: CFR+'s regret is floored at zero and never
    decreases, so a rare-but-persistent *downward-biased* placeholder
    (1/n_live systematically understates a genuinely strong hand's real
    equity, unlike a flat coinflip which happens to be closer for some
    hands) doesn't average out over more iterations the way ordinary
    noise would — it compounds. The exact "gets worse with more
    iterations" signature `cfr.py`'s own `EXPLORATION_EPSILON` docstring
    already diagnosed for a different mechanism during M8, showing up a
    second time via a different one.
  - **Three real mitigations were built, each measured, none alone
    sufficient — reported honestly rather than the first one that
    "looked like it worked" being shipped unverified:**
    1. `mccfr_solve` (`poker_solver/cfr.py`) now rejects an infeasible
       opponent-hand draw and resamples the whole tuple (capped at
       `MAX_OPPONENT_RESAMPLE_ATTEMPTS = 50`, falling back to proceeding
       anyway only for a pathologically degenerate hands pool where no
       draw could ever succeed) instead of proceeding and needing a
       placeholder at all. This alone measurably helped KK but left
       AKs/QQ/AKo essentially unchanged — traced to why: it only
       eliminates the case where the *opponents* conflict among
       themselves; it can't touch the separate case where one specific
       *candidate* hand (evaluated, by MCCFR's own vectorized design,
       against every sampled opponent tuple regardless of whether that
       candidate is what the traverser actually holds) conflicts with
       an otherwise-perfectly-feasible opponent tuple.
    2. `traverser_equity_vector` itself now tries one more, thorough
       search before falling back on a blocked candidate: a fresh JOINT
       `deal_n_hands` call over the candidate *and* every opponent
       together, not reusing the first (arbitrary) opponent assignment
       `deal_n_hands`'s own backtracking happened to find first — a
       different, equally-valid concrete assignment of the same
       opponent classes often leaves room for the candidate after all.
       Real, measurable, still not sufficient alone.
    3. `_pairwise_fallback_equity` (new) replaces the flat `1/n_live`
       placeholder entirely, for the residual cases where no valid
       joint assignment exists at all: the candidate's mean *pairwise*
       Monte Carlo equity against each opponent individually (reusing
       `monte_carlo_equity`, at a smaller `FALLBACK_PAIRWISE_SAMPLES =
       50` since this path is rare by design) — the same "ignore
       blockers between players' hands" approximation this project has
       used since v1, applied here for the first time to a fallback
       value instead of a primary one. Hand-aware where `1/n_live` was
       blind to hand identity (confirmed directly: AA's fallback against
       a fixed opponent tuple lands meaningfully above 72o's fallback
       against the *same* tuple, where the old flat placeholder gave
       them the identical number) — genuinely better, and still not a
       full fix.
  - **The load-bearing finding that kept this from turning into an
    unbounded investigation: the pre-M27 code shows the exact same
    non-monotonic instability, just biased in the opposite direction.**
    Re-running the *original* buggy code (`git stash`) at 300 / 3,000 /
    30,000 iterations showed AA's `all_in` frequency swinging
    34% -> 11% -> 79% — not stable, just differently wrong (over-jamming
    instead of over-folding, since the old flat `0.5` inflates value in
    the opposite direction). This is strong evidence the underlying
    instability is a **pre-existing MCCFR convergence sensitivity at
    6-max with this project's small, top-heavy demo hand pool
    (`DEMO_MULTIWAY_HANDS`, shared with `test_solver.py`'s `_M9_HANDS`)
    — not something this fix introduced, just something the old bug's
    own extreme, one-sided distortion had been masking** by keeping
    strategies pinned toward one extreme rather than letting them swing.
    Properly solving it is a materially bigger undertaking (most likely
    restructuring CFR+'s regret update to *mask out* a hand's
    contribution for an iteration entirely rather than feed it any
    placeholder value, a real architectural change to `_mccfr_recurse`,
    not attempted here) — correctly out of scope for what recommendation
    #1 asked for, and flagged as real, separate future work rather than
    silently left for someone to rediscover.
  - **The pragmatic mitigation actually shipped, mirroring a precedent
    this project already established for exactly this situation:**
    `MULTIWAY_TABLE_CONFIGS[6]`'s iteration budget cut from 30,000 to
    300 (`api/main.py`) — matching 9-max's own already-conservative
    number, the same "smaller, deliberately conservative" move M9 made
    for 9-max, not a number this milestone specifically validated as
    sufficient (no iteration count tested was fully stable; even AA/KK
    weren't consistently under 5% across different seeds at 500
    iterations). `tests/test_solver.py`'s `six_max_result` fixture
    matches, and `test_six_max_utg_premium_hands_rarely_fold` was
    renamed to `test_six_max_utg_aa_rarely_folds` and narrowed to only
    assert AA tightly — mirroring `test_nine_max_utg_aa_rarely_folds`'s
    own established "only assert what's actually reliable" pattern,
    since AA (not KK, not AKs, not QQ) was the one hand that held up
    consistently across seeds during this investigation. The API's own
    module docstring (`api/main.py`) is corrected too: it used to claim
    6-max "reaches good convergence in minutes" at 30,000 iterations —
    that claim didn't survive this milestone's testing.
  - **Scope note:** this milestone deliberately pulled forward a slice
    of recommendation #3 (opponent-sampling card-removal awareness) —
    the resampling fix above — once investigation showed recommendation
    #1 alone wasn't sufficient to ship responsibly; the *rest* of
    recommendation #3 (a genuinely joint, card-removal-aware sampler,
    the more principled fix for the deeper 6-max issue this milestone
    documents but doesn't resolve), recommendation #2 (test-coverage/
    confidence-signal gap), and recommendations #4-7 remain untouched,
    as scoped.

- **M28 — The confidence signal recommendation #2 asked for.** M27's own
  tests already closed that recommendation's *coverage* half (real
  3+-opponent tests, real asserted values, not just bounds-checks). This
  closes the other half: `docs/full-table-diagnostic-2026-08.md`'s §3.3
  itself — "no way, in the code or the API response, to tell a converged
  answer from one nobody ever computed" — 88% of a real 9-max solve's
  touched nodes measured with at least one hand at exactly zero
  `strategy_sum`, silently indistinguishable from a real result.
  - `InfoSetTable.trained_mask()` (`cfr.py`) exposes exactly the
    condition `average_strategy()` already computed internally
    (`strategy_sum.sum(axis=1) > 0`) and discarded — a boolean per hand,
    not a new accumulator, so no change to what CFR actually tracks.
    `StrategyResult.trained_hands(node)` / `.trained_for_position(pos)`
    (`solver.py`) are new, parallel methods — not a change to
    `strategy_at`'s own shape, so every existing caller (`opening_range`,
    `strategy_for_position`, every prior milestone's own tests) keeps
    working unchanged. Wired into the response layer: `format_solve_
    response`/`format_flop_response` (`strategy_format.py`) both gained
    a `trained` field alongside their existing strategy field, and
    `SolveResponse`/`FlopSolveResponse`/`TurnPathQueryResponse`
    (`api/schemas.py`) all gained the matching typed field — live on
    `GET /solve/{stack_bb}` (every player count — the diagnostic's own
    literal citation), `/solve_flop`, `/solve_flop_turn`, `/solve_flop_
    to_river`, and `/solve_turn_from_path`.
  - **A real, non-obvious finding, caught by testing against a genuine
    flop result rather than assumed:** `trained=False` turns out to have
    two distinct, equally valid causes, not one. The original diagnostic
    concern — MCCFR never sampling a hand at a node — is one. The other,
    found while writing `format_flop_response`'s own test: a postflop
    combo-level result's `self.hands` is the *union* of both positions'
    ranges, so a hand that's entirely villain's (zero weight in hero's
    own range by construction) has zero reach for hero throughout the
    *whole* solve, at any iteration count — not "wasn't sampled yet,"
    structurally can never be hero's hand at all. Both correctly read as
    "don't trust this number," just for different reasons; documented
    explicitly in `trained_hands`'s own docstring rather than left as a
    surprise the next caller has to rediscover.
  - **A deliberate, stated scope boundary, not a silent gap:**
    `/solve_flop_cached` and `/solve_flop_from_path` do *not* expose
    `trained` — both are backed by `poker_solver.library`'s canonical
    cache (M20-M24), which persists only a `LibraryEntry`'s already-
    flattened `strategy` dict, not the live `StrategyResult`/`node_data`
    a confidence signal needs to be computed from. Adding it there means
    changing `LibraryEntry`'s own persisted shape (and `save_library`/
    `load_library`'s JSON format) — a real, separate piece of work
    against a foundational, already-shipped data structure, correctly
    out of scope for a same-milestone add-on.
  - **Frontend:** `RangeGrid`'s cells fade (opacity + dashed outline,
    not a color change — a 9-max grid can be *mostly* untrained, so a
    loud per-cell treatment would drown out the grid's own real signal)
    when `trained[hand] === false`; `DetailPanel` shows a full warning
    sentence when the *selected* hand is untrained (the place a user
    looks closely at one hand's exact numbers and might act on them —
    deliberately louder than the grid's own glance-level fade);
    `Legend` gained a small swatch explaining the fade. `FlopSolver`/
    `TurnPathSolver`'s own per-combo row lists get a lighter-weight
    `.trained-indicator` pill (mirroring M22's own `.hit-indicator`
    idiom) — expected to appear rarely there, since postflop solving is
    still heads-up/exact throughout. A hand absent from a `trained` map
    entirely (including the pre-load `null` state) defaults to trained
    — every real response's `trained` map covers exactly the same keys
    as its strategy map, so "absent" only means "not wired through yet"
    or "nothing has loaded," not "known bad."
  - **Verified end to end through the real HTTP layer, not just the
    engine:** a live `GET /solve/100?players=9&position=BB` response's
    own `trained` map has at least one `false` entry — the diagnostic's
    88%-untrained finding, now visible in the actual API response a
    real caller receives, not just an internal measurement.

- **M29 — The heads-up-flop-after-multiway-preflop unlock (diagnostic
  recommendation #4), fully wired live.** §4's own "one piece of good
  news": the single most common way a real full-ring hand actually
  reaches a flop — everyone folds except two players — was already
  ~95% wired (range derivation was already N-general, the stack-
  equality guarantee already held at any N), blocked only by "one
  overly strict guard" plus "three separate places each doing their
  own brittle two-position unpack." Closes all of it, plus the live
  endpoint + frontend wiring the diagnostic explicitly left for later.
  - **The real poker rule, verified before writing any code, not
    guessed:** `game_tree.py` gained `button_position`/
    `postflop_action_order`. A design-validation pass caught the
    naive framing backwards: there's no "small blind acts first
    postflop, except at heads-up" rule — the universal rule (Robert's
    Rules of Poker) is stated relative to the **button** ("action
    begins with the first active player to the left of the button,"
    no table-size exception, ever). What's genuinely heads-up-specific
    is a *seating* fact, not a betting-order exception: the button
    posts the small blind and *is* the small blind at N=2, so
    `button_position` is `positions[0]` there and `positions[-3]`
    (the seat immediately before the small blind) at N>=3 — one real
    exception, cleanly isolated to locating the button, not smeared
    across the postflop-order formula itself. Verified exhaustively,
    not just on hand-picked examples: every 2-survivor subset of every
    real table size this project ships (55 cases) checked against an
    independently-written reference implementation (a different code
    shape — index-stepping the ring one seat at a time, not slicing —
    mirroring M19's own brute-force-vs-naive-walk validation
    technique), all 55 agreeing.
  - **A correction to the diagnostic's own accounting, caught by
    tracing the real call sites, not assumed from its prose:** the
    diagnostic attributed one of the "three duplicated unpack" sites to
    `solver.py`'s `derive_flop_scenario` — that function does no
    position unpacking at all (it takes `raiser_position`/
    `caller_position` as explicit parameters). The real third site is
    `api/main.py`'s `_query_turn_from_path` (M26), added *after* the
    diagnostic's own snapshot. All three real sites —
    `poker_solver/library.py`'s `query_strategy_from_path`, and
    `api/main.py`'s `_query_flop_from_path` (M24) and
    `_query_turn_from_path` (M26) — now share `postflop_action_order`
    instead of each guessing `positions[0]`/`positions[1]`.
    `query_strategy_from_path`'s own guard changed from rejecting any
    multiway-*origin* result outright to the real constraint: fewer
    than 2 live positions surviving *to the terminal* — a fact about
    the survivors, not the origin table size (its own stated
    justification for the old guard was right about needing the full
    seating order, and wrong about the conclusion: `result.config.
    positions` already *is* that order, sitting right there on the
    object passed in).
  - **A second, real blocker found during design validation, not in
    the original diagnostic — and directly acted on, not just noted:**
    `derive_ranges_from_path`'s reach-multiplication has no confidence
    signal (M28 stops at a single node's `trained_hands`). Measured on
    a real 6-max solve at its shipped budget: a shallow "everyone folds
    to a call" path was cleanly 6/6 trained with well-differentiated
    ranges, but a deeper 3-bet line left both actors' own decision
    nodes 0/6 trained, with one derived range coming back *exactly*
    uniform (0.25 for every hand checked) — confident-looking,
    fabricated, and silently indistinguishable from a real one. Fixed
    at the source, not routed around: `PathScenario` gained a `trained`
    field (position -> {hand: bool}), mirroring `ranges`' own shape,
    computed the identical way reach itself composes — the AND, not
    just the last step, across every node a position acts at along the
    path (one untrained step anywhere is enough to make the whole
    derived frequency suspect, the same way one bad factor corrupts a
    product). 3-max (100K-iteration budget) stayed clean on every path
    traced, including the same deep-3-bet shape — this is specifically
    a 6/9-max (small iteration budget) finding, not a general one.
  - **Live wiring, the user's own explicitly-chosen "most thorough"
    option — not deferred to a follow-up milestone the way M23-before-
    M24's own precedent might have suggested:** `_get_or_solve_
    preflop_raw` (`api/main.py`) gained a `players` parameter — 2
    (unchanged) solves heads-up with the caller's own `iterations`;
    any other supported size delegates outright to `_get_or_solve_
    multiway`, reusing (not duplicating) its cache — a user who already
    loaded that table size's range chart triggers no redundant second
    solve opening the wizard next, and vice versa (same fixed-iteration-
    budget discipline `MULTIWAY_TABLE_CONFIGS` already enforces
    elsewhere, extended here rather than reopened). `POST /preflop_
    walk`, `/solve_flop_from_path`, `/solve_turn_from_path` all gained
    a `players` request field (default 2); every partition/cache key
    that keys on the action path (`_path_query_libraries`,
    `_turn_path_cache`) now also keys on `players` — a real, not
    hypothetical, collision risk: the identical literal action-kind
    path can be legal at two different table sizes and mean two
    different things (proven with a live test: the same path partitions
    separately at players=2 vs. players=3).
  - **Frontend:** `ActionPathSolver.tsx`/`TurnPathSolver.tsx` both
    gained a table-size toggle (mirroring `TableModeControl`'s own
    visual language, but not the component itself — a step-by-step
    wizard has no "which position am I viewing" concept, the whole
    reason that component also always renders a position selector,
    which wouldn't fit here). Switching table size resets the action
    path (a path walked against one tree isn't meaningful against
    another) and re-walks live. Each component's own curated *preflop*
    presets (hand-authored against the heads-up 2-position tree shape)
    are hidden — not shown-broken — at any other table size; `TurnPath
    Solver`'s separate *flop*-line presets stay unconditional at every
    table size, correctly, since the flop-level tree `solve_flop_turn`
    builds is always 2-position regardless of how many players the
    origin hand started with. Live-verified end to end: a real 3-max
    hand (BTN opens, SB folds, BB calls) reaches a real heads-up flop
    and turn decision through both wizards.
  - **A known, deliberate gap, named rather than silently left:**
    `PathScenario.trained` (the fix above) isn't yet surfaced in either
    live endpoint's response — a real, measured-to-matter signal at
    6/9-max specifically, but exposing it well needs its own response-
    shape decision (per-hand, like `format_solve_response`'s own
    `trained`, or a per-position summary), deferred rather than bolted
    on to an already-large milestone. The engine-level fact is real and
    tested regardless of whether the API surfaces it yet.

- **M30 — N-way, board-aware combo equity (Phase 1 of recommendation
  #5, "true multiway postflop solving").** The diagnostic's own §4
  scoped this out explicitly as needing *four* separate pieces, not
  one: this equity primitive itself, plus three MCCFR-side changes —
  a signature-level change threading a per-chance-branch equity source
  through MCCFR's terminal-value computation; per-position range
  seeding and opponent sampling (MCCFR currently has no way to seed a
  position's real derived range, and samples every opponent from one
  global preflop-style prior regardless of position); and a
  chance-branch sampling case in the MCCFR recursion itself. M30 ships
  only the first, mirroring this project's own M10-then-M11/M17-then-
  M18 precedent of a measured, standalone primitive before any live
  wiring — the other three remain explicitly unscoped future work, not
  attempted here.
  - **The gap, precisely stated:** neither existing equity primitive
    has both properties true multiway postflop solving needs at once.
    `equity.py`'s `MultiwayEquityCache` is N-way but always deals a
    fresh random 5-card board from nothing (preflop-only, board-blind).
    `board_equity.py`'s `build_board_equity_table` is board-aware but
    strictly pairwise (two combos at a time, never more). New module
    `poker_solver/multiway_board_equity.py` — not folded into either
    existing module, matching `combos.py`/`board_equity.py`'s own M10
    precedent of a fresh module rather than retrofitting one with a
    different existing shape — is their intersection: given a real,
    fixed board and a fixed tuple of opponent combos,
    `nway_combo_equity_vector` computes each candidate in a pool's real
    N-way win-share (a k-way tie splits 1/k) on that specific board;
    `NwayBoardEquityCache` adds the same lazy, memoized-by-opponent-
    tuple architecture `MultiwayEquityCache` already established at M8,
    adapted for board-awareness and combo- (not class-) level
    granularity.
  - **Verified against the existing, trusted primitive before writing
    any formal tests, not just trusting the new code on its own:** at
    N=2 (1 fixed opponent), this module's output was compared directly
    against `build_board_equity_table`'s already-shipped, already-
    trusted pairwise result at the identical seed — an exact match
    (0.9180 == 0.9180). Carried into the formal suite
    (`tests/test_multiway_board_equity.py`, 22 tests, all passing) as a
    permanent regression test, alongside a complete-river-board
    exact-value cross-check, NaN handling for every impossible-
    combination case (candidate blocked by the board, candidate blocked
    by an opponent, opponents mutually conflicting with each other or
    the board), N-way sanity checks (a hand-verifiable exact
    quad-aces-vs-two-others river case; a genuine 3-way tie confirmed
    to split exactly 1/3), exact — not sampled — resolution at
    `remaining_needed<=1` for both turn and river boards (mirroring
    `board_equity.py`'s own identical optimization), and a full
    cache-behavior suite (starts empty, populates on touch, a hit
    doesn't grow it, order-independent, deterministic across
    separately-constructed caches sharing a seed, different boards
    don't collide).
  - **The load-bearing design call, carried forward from M27's own
    lesson rather than rediscovered the hard way a second time: no
    placeholder value, ever — NaN only.** A candidate that can't
    physically coexist with the fixed board/opponents gets NaN, full
    stop (the same convention `board_equity.py` already established for
    its own impossible matchups) — never a flat `1/n_live` or any other
    constant. M27 measured, at the preflop/class level, that injecting
    *any* constant placeholder for a combination an opponent sampler
    reaches without card-removal tracking compounds destructively under
    CFR+'s regret flooring; what actually worked there was rejecting
    and resampling *before* ever needing a placeholder. This module's
    own docstring states explicitly, for whichever future milestone
    wires it into MCCFR: apply that same reject-and-resample discipline
    at the call site, don't invent a fresh placeholder-value question
    this module's NaN convention has deliberately left unanswered.
  - **A second, smaller precision call:** each candidate deals its
    *own* Monte Carlo runouts, correctly excluding that candidate's own
    two cards from its own deck — not shared across candidates the way
    `chance.py`'s M12 entry names as an accepted, precisely-bounded
    approximation elsewhere (uniform chance-branch weight regardless of
    a hand's own blockers). This module didn't need to accept that
    shortcut, since nothing here forces runout-sharing the way a
    pre-built chance tree does.
  - `_stable_seed` is a small, local, duplicate copy of `equity.py`'s
    own private helper of the same name/shape, not a shared import —
    confirmed, not assumed, that this is the actual established
    practice: `cards.remaining_deck`'s own promotion (replacing a
    private `_remaining_deck` in two places) was the one time this
    project *did* share a utility, and it happened by promoting an
    already-duplicated implementation after the fact, not by a new
    module reaching back into an old one's private helpers up front.
  - **Measured, not assumed — and a real, structurally better-scaling
    shape than `board_equity.py`'s own bottleneck, found while
    measuring, not before:** single-candidate cost vs. growing opponent
    count (directly comparable to the diagnostic's own earlier
    back-of-envelope prototype benchmark): N=2 players/500 samples
    13.58ms, N=2/2000 55.25ms, N=9/500 57.78ms, N=9/2000 252.58ms — the
    same ballpark as the diagnostic's own prototype numbers (14.75ms /
    286.46ms at the matching two points), real production code landing
    a little faster, not slower. The more realistic shape — a real
    candidate pool against one fixed opponent tuple, mirroring
    `traverser_equity_vector`'s own actual call pattern — costs 159ms
    (2 opponents, 20-combo pool) up to 1.17s (8 opponents, 50-combo
    pool), all at 200 samples: **linear in pool size**, not the O(N²)
    pairwise cost that was M10/M17/M18's own real bottleneck, because
    this module evaluates each candidate against one *fixed* opponent
    tuple rather than building a full pairwise table. `NwayBoardEquity
    Cache`'s hit/miss ratio, measured on the largest (8-opponent,
    50-combo) case: a miss costs ~1.15s (matches the bare-function
    number above, as expected — a miss *is* one bare-function call), a
    hit averages **0.0037ms** across 1,000 repeats — a ~310,000x ratio,
    the same "cache a fixed-opponent-tuple lookup, make repeats nearly
    free" shape `MultiwayEquityCache`/`library.py`'s own caches already
    established, now confirmed to hold for this new, differently-shaped
    primitive too.
  - Full suite re-run, not just the new module's own tests: 575 backend
    (`python -m pytest tests/ -v`) + 139 frontend (`npm test`), zero
    regressions — confirms this genuinely-standalone addition (zero
    existing callers, zero existing files modified) didn't perturb
    anything already shipped.
  - Engine only, no `api/main.py`/frontend changes, zero changes to any
    existing module — matches `combos.py` (M10)/`canonicalize.py`
    (M19)/`library.py` (M20)'s own standalone-new-primitive precedent.
    The three remaining recommendation-#5 prerequisites the diagnostic
    named (MCCFR terminal-value threading, per-position range
    seeding/opponent sampling, MCCFR chance-branch sampling) are each
    likely their own milestone, mirroring the real-time-speed roadmap's
    own M17-M21 multi-phase structure — none attempted here.

- **M31 — Per-position range seeding for MCCFR (Phase 2 of
  recommendation #5).** `mccfr_solve` (`poker_solver/cfr.py`) previously
  drew every position's hole cards — traverser and opponents alike —
  from one shared, global `combo_weight`-derived prior, regardless of
  position: real for preflop-from-scratch (every seat's honest prior
  before any action *is* uniform combo_weight, which is why this went
  unnoticed for 20+ milestones), but wrong for the eventual postflop
  case this whole recommendation exists to unlock, where different
  positions have genuinely different, already-narrowed ranges. New
  `initial_reach: dict | None = None` parameter — deliberately named
  and shaped to exactly match `solve()`'s own existing parameter of the
  same name (position -> a weight array, same length/order as `hands`)
  — so a caller who already understands one already understands the
  other, and a future milestone reusing `derive_ranges_from_path`
  (already N-player-general per M16) has a matching shape to bridge
  into on both the exact-solve and MCCFR paths alike.
  - **Threaded into BOTH halves of sampling, not just one:** the
    traverser's own `reach` (their belief over their own hand, seeded
    once per iteration) now comes from their own weight vector, and
    each opponent's hand is now independently sampled from THEIR OWN
    weight vector too — previously both read the same shared
    `combo_weights` array unconditionally. A position missing from
    `initial_reach` (or the default `None`, meaning no overrides at
    all) falls back to `combo_weight`, computed lazily — mirroring
    `solve()`'s own identical `_default_reach` reasoning exactly,
    including the same forward-looking justification: `hands` may
    someday be `combos.HandCombo` (a future postflop MCCFR consumer),
    which has no `combo_weight` attribute at all, so a caller supplying
    every position's own real range should never touch the fallback.
  - **A real refactor alongside the feature, not just new code bolted
    on:** the opponent-sampling-and-resample-on-infeasibility loop was
    extracted from `mccfr_solve`'s own body into a new, independently
    testable `_sample_opponent_hands` — unchanged behavior (same
    `MAX_OPPONENT_RESAMPLE_ATTEMPTS` retry ceiling, same "proceed with
    the last infeasible draw" fallback M27 already established), but
    now directly unit-testable against a real per-position weight
    vector without needing to reverse-engineer sampling behavior from a
    full solve's aggregate regret/strategy output — the specific thing
    that made verifying the *opponent* half of this feature tractable
    at all (the *traverser* half is directly observable through
    `InfoSetTable.trained_mask()` at the traverser's own decision node;
    nothing analogous exists for a sampled-not-accumulated opponent
    draw).
  - **Validates upfront, before any solving happens, not partway
    through:** a wrong-length weight vector or one that sums to zero
    (that position would have no possible hand to ever be sampled as,
    whether traversing or acting as an opponent) raises `ValueError`
    immediately — the same "fail loudly at the API boundary" discipline
    `solve()`'s own `equity_table.shape` check already established.
  - **The single most important regression guarantee, proven, not just
    argued:** `initial_reach=None` (the default, and every pre-M31 call
    site) must be *exactly* equivalent to every position explicitly
    supplying its own `combo_weight`-derived array — confirmed with a
    dedicated test comparing the two calls' full `node_data`
    (`regret_sum`/`strategy_sum` at every node) for bit-for-bit
    equality, not just "produces a similar-looking strategy." Given
    M27's own history with this exact function (a change that looked
    "small, localized" turned out not to be, caught only by the
    mandatory full-suite rerun), the full backend suite — not just
    `test_cfr.py` — was re-run before trusting this: 582 passed, zero
    regressions, including every existing 3-max/6-max/9-max preflop
    test and the N=2 exact-solver cross-validation test this module's
    own docstring cites as MCCFR's strongest correctness signal.
  - **New capability, verified end to end, not just structurally:** a
    dedicated test seeds one position's `initial_reach` with zero
    weight on a specific hand, in a real 3-max preflop tree (not a toy
    2-action one), and confirms that hand shows `trained_mask()==False`
    at that position's own root-level node after solving — the same
    real "zero reach weight" `trained=False` cause M28's own
    `trained_hands` docstring already documented as one of two possible
    causes, now deliberately engineered rather than incidentally
    discovered. A second test confirms opponent-side sampling
    specifically: a position's weight vector concentrated entirely on
    one hand is sampled as that exact hand across 200 independent
    draws, never anything else. A third, end-to-end test confirms two
    solves differing *only* in one position's `initial_reach` (same
    seed, same tree, same equity cache otherwise) produce genuinely
    different `strategy_sum` — proving the parameter isn't silently
    ignored anywhere in the pipeline.
  - **Zero real callers today, by design — matches M17/M19's own
    standalone-primitive precedent, not an oversight:** `solver.py`'s
    multiway dispatch always solves a full preflop tree from its root,
    where uniform `combo_weight` genuinely is every position's honest
    prior — there is no real situation *yet* where a different weight
    vector would be the more correct choice. The real consumer is a
    future milestone (seeding genuine per-position ranges into a
    multiway postflop MCCFR solve), itself still blocked on the two
    OTHER recommendation-#5 prerequisites this milestone deliberately
    doesn't attempt: a board-aware, per-chance-branch equity source
    (`multiway_board_equity.py`, M30) actually threaded through this
    module's terminal-value computation, and a chance-branch sampling
    case in `mccfr_solve`'s own recursion, which doesn't exist at all
    yet (`_mccfr_recurse` has no `ChanceNode` handling, unlike
    `_solve_recurse`'s M12-era one).
  - Engine only, no `api/main.py`/frontend changes — matches every
    other phase of this recommendation and the real-time-speed
    roadmap's own M17/M19 precedent for a primitive milestone.

- **M32 — MCCFR chance-branch sampling + board-aware terminal equity
  (Phase 3 of recommendation #5, closing it).** The diagnostic's own §4
  framed what remained as *two* separate prerequisites — a signature-
  level change threading a per-chance-branch equity source through
  MCCFR's terminal-value computation, and a chance-branch sampling case
  in MCCFR's own recursion. Design analysis this session found these
  aren't separable: sampling a chance branch produces a subtree whose
  terminals need *some* equity source, and that source has to be M30's
  board-aware primitive. This milestone ships both together, closing
  recommendation #5's engine-level work (a live endpoint/`solver.py`
  entry point remains explicitly future work, mirroring M12+M13-before-
  M14's own precedent).
  - **A Plan-agent design pass (mirroring M27's own precedent for a
    similarly high-stakes `cfr.py` change) surfaced three real findings
    via actual code execution, not assumption, before any implementation
    — the kind of thing a plan built from reading alone would have
    missed:**
    1. `mccfr_solve` could not run against a `HandCombo` pool at all —
       confirmed by execution: `_sample_opponent_hands` unconditionally
       called `equity.deal_n_hands`, which reads `StartingHand`-only
       attributes and raises `AttributeError` on a `HandCombo`. Fixed
       with a new `_opponent_hands_are_dealable` — dispatches on
       `isinstance(hands[0], HandCombo)`: a plain pairwise
       physical-card-overlap check for combos (exact, not an
       approximation — combos already *are* concrete cards, no search
       needed), byte-for-byte the existing `deal_n_hands`-via-try/except
       behavior for `StartingHand`. This is the direct application M30's
       own docstring already asked for ("reject-and-resample opponent/
       candidate combos before calling this function with something
       already known to be impossible").
    2. `game_tree.postflop_action_order` (M29) — the obvious-looking
       helper for filtering a folded position out of the next street —
       is WRONG when applied to an already-postflop-native
       `StreetConfig.positions` tuple: confirmed by direct execution
       (`postflop_action_order(("OOP","MID","IP"), live=("OOP","IP"))`
       returns `('IP','OOP')`, the wrong order). `postflop_action_order`
       exists specifically to convert a *preflop* `GameConfig.positions`
       tuple into postflop order; a `StreetConfig`-shaped tuple is
       already postflop-native (first entry already acts first, by that
       config's own construction) — nothing to re-derive. Fixed with the
       plain filter `tuple(p for p in positions if p not in folded)`,
       matching the exact idiom `solver.py`'s own `derive_ranges_from_
       path` already uses. Regression-tested directly (a 3-position
       fixture with the middle position folded, asserting the correct
       live-position order) — specifically shaped to fail under the
       tempting-but-wrong `postflop_action_order`-based approach.
    3. `multiway_board_equity.NwayBoardEquityCache.equity_vector` needed
       renaming to `.traverser_equity_vector` — a naming inconsistency in
       M30's own shipped code, invisible until now because M30 shipped
       with zero real callers. The rename makes `NwayBoardEquityCache` a
       true duck-typed drop-in for `MultiwayEquityCache` at any
       `equity_cache`-shaped call site, which this milestone's whole
       design depends on: `_mccfr_terminal_value`'s signature and body
       needed **zero changes** beyond one new line (see below) to accept
       a board-aware equity source instead of a board-blind one.
  - **A fourth point, caught in this session's own review of the agent's
    proposal before writing any code — the one place "mirror an existing
    precedent" was the wrong call:** the agent proposed handling
    `NwayBoardEquityCache`'s NaN outputs (a candidate combo physically
    conflicting with the board/fixed opponents — a *structural*,
    high-frequency fact at postflop combo granularity, not a rare
    sampling artifact) via `nan_to_num(equity_vector, nan=0.5)`, directly
    mirroring `chance.py`'s own already-accepted, already-shipped
    `nan_to_num(..., nan=0.5)` precedent for the *exact* solver. That
    precedent is safe *there* specifically because the exact solver
    averages over ~47 branches every iteration, diluting a 0.5-biased
    minority into a mostly-real-valued average — a materially different
    regime from M27's own hard-won lesson, that a flat placeholder value
    repeatedly encountered across many *sampled* iterations (not diluted
    by within-iteration averaging) can compound destructively under
    CFR+'s regret-flooring ratchet. Given postflop combo-level blocking
    is common (not rare like M27's own opponent-infeasibility case),
    this was judged close enough to M27's own regime to require the same
    caution — implemented anyway (the simpler, better-justified choice),
    but treated as unproven until M27's own validation methodology
    (an iteration-count sweep, checking for the exact "grows monotonically
    with more iterations" signature M27 diagnosed) was actually applied,
    not just assumed safe by analogy.
  - **The mandatory empirical stress test, run before shipping, not
    after — and the real result is the opposite of a compounding bug:**
    a real 3-max flop scenario (a wider, deliberately-overlapping 5-combo
    pool per position — more blocking between positions' own combos than
    the tiny end-to-end test uses, the exact condition that produces NaN
    entries and exercises the new `nan_to_num`), chance-dispatched to a
    real turn, tracking a mid-strength hand's (AKo) and a weak pair's
    (44) fold frequency facing a raise at 300 / 3,000 / 30,000
    iterations: **0.2749 -> 0.0380 -> 0.0100** (AKo) and **0.1829 ->
    0.0379 -> 0.0145** (44) — *decreasing* and *stabilizing* (shrinking
    deltas: -0.24 then -0.03, not growing), the precise opposite of M27's
    own diagnosed signature (which grew monotonically toward a degenerate
    extreme, e.g. 22.8% -> 69.2% -> 94.8%). Zero `average_strategy()`
    tables showed a NaN entry at any of the three iteration counts (0 of
    936, 2,863, and 4,413 InfoSets respectively) — the `nan_to_num` fix
    is doing its job, not leaking. Concluded, not assumed: the simpler
    `nan=0.5` approach ships as-is; the stricter net-payoff-masking
    alternative considered during design was not needed. A real,
    incidental finding from the same sweep, worth recording: per-
    iteration cost *dropped* as iteration count grew (52ms/iter at 300,
    48ms/iter at 3,000, 15ms/iter at 30,000) — `chance_branches` (distinct
    dispatched (terminal, card) pairs, each memoized in `chance_data`)
    grew only ~1.6x (1,147 -> 1,857) while iterations grew 10x, meaning
    an increasing fraction of later iterations hit an already-warm
    chance-branch cache rather than paying fresh `NwayBoardEquityCache`/
    `build_street_tree` construction cost.
  - **Implementation, engine only:** new `chance.SampledChanceBranch`
    (frozen dataclass: `card`, `board`, `equity_cache`, `root`,
    `chance_fn`) + `chance.build_mccfr_chance_branch` — the MCCFR-native
    sibling of `ChanceBranch`/`build_chance_node`, deliberately NOT
    reusing either (a `ChanceBranch`'s `equity_table` is a precomputed
    NxN array for the exact solver's pairwise value representation; a
    `ChanceNode` eagerly builds *every* possible card's branch upfront —
    both wrong shapes for MCCFR, which samples exactly ONE card per
    visit and would otherwise pay for 44-49 N-way equity caches to serve
    one). `card` is a parameter, not sampled inside the function — the
    sampling decision (via `mccfr_solve`'s own seeded `rng`, for the same
    determinism story every other sampling decision in this module
    already honors) belongs in `cfr.py`, and lets `cfr.py` check its own
    `chance_data` memoization *before* paying construction cost.
    Structural double-dispatch prevention mirrors `build_chance_node`'s
    own placement exactly: `chance_fn=None` lives inside the *same*
    `if remaining_stack == 0` block that decides `root` for an
    all-in-already branch, not a separate check applied after.
  - `cfr.py` — `mccfr_solve`/`_mccfr_recurse` both gain `board`/
    `chance_fn`/`chance_data` (mirroring `solve()`'s own parameter names
    exactly; `board` has no `solve()` analog, needed because MCCFR must
    *sample* a specific next card rather than dispatch to a pre-built
    exhaustive structure). New `_sample_chance_card` — plain uniform
    sampling from the remaining deck, excluding the board and every live
    opponent's own cards, via `mccfr_solve`'s own `rng`. Deliberately no
    `EXPLORATION_EPSILON`-style floor (a chance card's distribution is
    fixed, uniform, exogenous "nature" randomness, never a *learned*
    policy that can degenerate to an exact 0/1 split the way
    `current_strategy()` can — no analogous collapse risk to guard
    against) and no `MAX_OPPONENT_RESAMPLE_ATTEMPTS`-style retry loop
    (excluding known-conflicting cards *before* drawing makes the single
    draw correct by construction, unlike opponent-hand sampling's
    needs-retry-after-the-fact shape).
  - **A design simplification caught during this session's own
    implementation, not carried over from the Plan agent's proposal
    verbatim:** the agent's dispatch gate was `chance_fn is not None and
    node.is_showdown and traverser not in node.folded`, wrapped around a
    separate `if other_live:` check. Algebraically, that wrapper is
    unreachable-false: `is_showdown` already means >=2 positions are
    live, and a live traverser is one of them, so at least one *other*
    live position is guaranteed whenever both outer conditions hold —
    proven once (in a comment at the call site) and removed, rather than
    re-verified at runtime on every single dispatch for a condition that
    can never actually be false there.
  - **Tests:** `tests/test_chance.py` — `build_mccfr_chance_branch`'s
    full validation surface (fold-out/board-conflict/negative-stack
    rejection, turn-street-vs-all-in-already root shape, determinism,
    every branch's own `chance_fn` is `None` per the one-hop scope) plus
    the direct Finding-2 regression test described above.
    `tests/test_cfr.py` — `HandCombo`-pool opponent sampling (works, and
    correctly resamples on a forced conflict); `mccfr_solve` omitting
    `board`/`chance_fn`/`chance_data` is bit-identical (full `node_data`)
    to passing all three explicitly `None`; `ValueError` when `chance_fn`
    is set without `board`; `_sample_chance_card` never returns a board
    or live-opponent card; a hand-built toy tree + spy `chance_fn` proves
    dispatch fires and the branch's own equity source is what actually
    gets used (not the outer one — a real arithmetic-level check, not
    just "the spy was called"); a genuinely 3-handed toy terminal (the
    traverser folded, two *other* positions still live and
    showdown-eligible) proves the MCCFR-specific fold gate, a divergence
    from `_solve_recurse`'s single `is_showdown` check that a 2-player
    toy tree structurally can't exercise; a branch whose own root is
    itself a showdown terminal falls through to direct equity, not a
    second dispatch (mirrors `test_solve_does_not_recurse_chance_fn_
    into_branch_subtrees`); all-in-already correctness at the `cfr.py`
    level; `chance_data` memoization (spy call count equals distinct
    `(terminal, card)` pairs actually dispatched — keyed by `id()`,
    since `TerminalNode` carries a dict field and isn't hashable — never
    the iteration count); the new `nan_to_num` line's own direct unit
    tests (replaces a stub NaN with neutral 0.5, and is a provable no-op
    against a cache that never produces NaN); full determinism given a
    seed with chance dispatch active; and the required end-to-end test —
    a real 3-max flop tree, a real per-position combo pool with
    `initial_reach` supplied for all three positions, a real board, a
    real `chance_fn`/`chance_data` wired through `build_mccfr_chance_
    branch` — asserting no NaN anywhere, every strategy row sums to 1.0,
    `chance_data` is non-empty (dispatch actually fired), and at least
    one dispatched branch reached a real turn-street `DecisionNode` (not
    just an all-in-reused terminal).
  - **Verification:** `python -m pytest tests/ -v` — 610 passed, zero
    regressions (up from M31's 582 — mandatory given `cfr.py`'s own M27
    history of "small, localized" changes turning out not to be, and
    that this touches `_sample_opponent_hands`, which every existing
    preflop MCCFR test depends on). `npm test` (frontend) — 139 passed,
    zero regressions (engine-only change, no frontend files touched).
  - **Scope:** engine only, no `api/main.py`/frontend changes, no
    `solver.py` entry point — matches M12+M13-before-M14's own precedent
    of shipping engine+tests before a live-wiring milestone. Turn->river
    chaining (a second hop) is deliberately deferred, mirroring
    M12-before-M13's own one-hop-first precedent; a `chain_to_river`-
    style forward-compatible parameter was considered (confirmed cleanly
    addable later with no late-binding-closure risk, unlike
    `build_chance_node`'s own loop-shared-variable concern, since this
    function builds exactly one branch per call) but not added now,
    matching the diagnostic's own explicit one-hop-first scope. This
    closes recommendation #5's engine-level work; a live endpoint (and
    the multiway-postflop `solver.py` entry point it would need) remains
    unscoped future work, the same "prove it small first, wire it up
    later" pattern the real-time-speed roadmap (M17-M21) and M15-M16
    each already established.

- **M33 — Suit-assignment bias + the determinism-claim fix
  (recommendation #7's first two items), plus closing recommendation
  #6's leftover documentation correction.** `docs/full-table-diagnostic-
  2026-08.md`'s §3.5 and §3.6 share one root cause: `deal_n_hands`'s
  backtracking search always tries each hand's candidate suit-pairs in
  `_suit_pairs_for`'s own fixed order, so a suited hand's first
  available suit is always the same one — confirmed directly: dealing
  several suited hands simultaneously (the real shape a 7-9 handed
  multiway showdown produces) always deals every one of them clubs
  (§3.5), and because `MultiwayEquityCache.traverser_equity_vector`
  dealt the caller's *raw* opponent-tuple order rather than its own
  already-canonicalized cache-key order, two orderings of the identical
  opponent tuple, on fresh caches, produced measurably different equity
  (§3.6 — the diagnostic's own reproduction: up to 0.0069 apart) even
  though the docstring claimed order-independence.
  - **The fix, one root cause, two changes:** `deal_n_hands` gained an
    optional `rng: random.Random | None = None` parameter (default
    `None`, every pre-M33 call site byte-for-byte unaffected — proven,
    not argued, via a dedicated test) — when supplied, each hand's own
    suit-pair candidates are shuffled (a fresh copy, not `_suit_pairs_
    for`'s cached list) before the search tries them, so which suit
    "wins" varies deterministically-given-`rng` instead of collapsing to
    one suit every time. `MultiwayEquityCache.traverser_equity_vector`
    now passes its own already-seeded `rng` into all three of its
    `deal_n_hands` calls (§3.5's fix) and deals `key` — the sorted,
    cache-key-canonical tuple — instead of the caller's raw
    `opponent_hands` order (§3.6's fix), closing the gap between the
    method's own docstring claim and what was actually true. `monte_
    carlo_equity_n` already accepted an `rng` parameter but never
    reached it into its own `deal_n_hands` call (only used it for the
    runout afterward) — threaded through too, while in the area, and
    caught by a spy-based test (`monkeypatch`), not inference.
  - **Callers that don't need this stay untouched, correctly:** `cfr.py`'s
    own opponent-feasibility checks (`_opponent_hands_are_dealable`)
    discard the dealt cards entirely, so suit bias there is inert —
    left as `rng=None`, no behavior change, no reason to pay any extra
    cost. `deal_two_hands` (the 2-player pairwise path, used by the
    heads-up preflop 169x169 table) is untouched too, matching the
    diagnostic's own explicit framing: "at N=2 (the originally-validated
    scale) this barely matters."
  - **A real, pre-existing test gap the diagnostic's own framing
    exposed, closed alongside the fix:** `test_multiway_cache_is_order_
    independent_for_opponent_tuple` (already in the suite) only proves
    the *same* cache instance's own sorted-key lookup hits one shared
    entry either way — trivially true from the cache key alone,
    regardless of whether the underlying deal is genuinely order-
    independent, since a repeat call on the SAME cache is a guaranteed
    hit no matter what. A new `test_multiway_cache_is_order_independent_
    across_fresh_caches` uses two separate, fresh caches (no shared-hit
    shortcut possible) — the diagnostic's own exact reproduction shape —
    and is the actual regression test for §3.6.
  - **A real, deliberate test-expectation break, fixed not glossed
    over:** `test_monte_carlo_equity_n_matches_pairwise_for_two_hands`
    asserted *exact* equality between `monte_carlo_equity_n` and the
    plain pairwise `monte_carlo_equity` at N=2, on the assumption that
    `deal_n_hands` and `deal_two_hands` consume `rng` identically for
    two hands. That assumption is now false by design — `monte_carlo_
    equity_n`'s own `deal_n_hands` call consumes extra `rng` draws for
    the new suit-shuffle that `deal_two_hands` (no `rng` support, by
    design, since N=2 doesn't need this fix) never did. Loosened to a
    generous Monte Carlo tolerance (`abs=0.1`) with a comment recording
    that the original exact-match expectation was an artifact of
    pre-fix behavior, not a property either function's contract ever
    promised.
  - **Measured, not assumed — including a real, controlled A/B check
    of the one performance question this fix could plausibly have
    raised:** isolated micro-benchmark (20,000 calls, 5 simultaneously-
    suited hands): `deal_n_hands` costs ~14.3μs/call without `rng`,
    ~17.8μs/call with it — a real but small (~24%) per-call overhead,
    negligible next to a real `traverser_equity_vector` cache-miss's own
    ~26ms. Confirmed the fix actually works, not just that it doesn't
    crash: 200 calls dealing the same 3 simultaneously-suited hands
    showed exactly one suit (`{'c'}`) without `rng`, all four suits with
    it. The full backend suite's own wall-clock time (617 tests, this
    milestone's own isolated run) measured ~46% higher than M32's own
    504s baseline (734s) — large enough to investigate properly rather
    than hand-wave as noise. Investigated with a real controlled
    comparison, not left as a guess: `git stash`/`git stash pop` around
    `tests/test_solver.py` alone (the single heaviest file — every
    6-max/9-max MCCFR fixture lives there, the actual workload that
    would show a real `deal_n_hands`-driven slowdown if one existed)
    measured **221.36s pre-fix, 220.90s post-fix** — statistically
    indistinguishable, well within normal run-to-run noise. This rules
    out the fix itself as the cause of the full-suite delta; the
    remaining, most likely explanation is ordinary machine-load
    variance between two measurements taken hours apart (M32's 504s
    baseline and this run), not a real per-call cost this milestone's
    change introduced — the micro-benchmark and the controlled
    single-file A/B both agree on that conclusion independently.
  - **Recommendation #6, closed in full:** its N>=3 Nash-equilibrium
    documentation half landed in M29; this milestone closes the other
    half the diagnostic named — correcting CLAUDE.md's own M9 entry,
    which attributed 9-max's small iteration budget to `MultiwayEquity
    Cache`'s cache-hit-rate collapse without also crediting what direct
    measurement (§3.2) found was the actual dominant cost driver (the
    `deal_n_hands` backtracking stall M27 later fixed) — see the
    correction inline in M9's own entry above.
  - **Scope:** engine only, no `api/main.py`/frontend changes. The third
    recommendation-#7 item — §3.10's three thread-safety gaps (an
    unlocked on-disk equity-table cache race, `MultiwayEquityCache.
    _cache`'s unlocked dict, `InfoSetTable`'s unguarded read-modify-
    write) — is a mechanically different kind of fix (locking, not
    equity computation) and is deliberately left for its own milestone,
    matching this project's "one coherent improvement per PR" rule.

- **M34 — §3.10's three thread-safety gaps, closing recommendation
  #7 in full.** Investigated each of the three named gaps individually
  before fixing anything, rather than treating them as one uniform
  "add locks" task — they turned out to have genuinely different risk
  profiles, and the fix (or deliberate non-fix) for each reflects that.
  - **Gap 1, real and already-live today, fixed with atomicity plus a
    lock:** `equity.get_equity_table`'s on-disk cache had an unlocked
    check-then-write race. Confirmed this isn't hypothetical: `api/
    main.py` already runs a background pre-warm thread that calls into
    `solve_preflop` -> `get_equity_table` concurrently with live
    requests (CLAUDE.md's own M14 entry already measured real
    contention between the two), so on a cold cache, two threads could
    both build the table and both `np.save` to the same path — a real
    torn-write risk. Fixed two ways, doing different jobs: a module-
    level `threading.Lock` avoids the *redundant* rebuild (a second
    thread re-checks `path.exists()` after acquiring the lock), and the
    write itself goes to a per-thread/per-process temp file then
    `os.replace`s into place (atomic on both POSIX and Windows) — so
    even a hypothetical caller reaching this function outside the lock's
    scope (a stated limitation: `threading.Lock` only protects threads
    within one process, not a multi-*process* deployment, which this
    project doesn't currently use) can never observe a partial file.
  - **Gap 2, not currently live but cheap to proactively harden:**
    `MultiwayEquityCache._cache` had the same class of unlocked check-
    then-write. Traced every real construction site in the codebase
    (confirmed, not assumed) before deciding this needed fixing at all:
    every one builds a fresh instance per solve, used single-threaded
    within it — no current caller actually shares one instance across
    threads. Fixed anyway, proactively: a `self._lock` guarding the
    dict read/write (never the expensive dealing/simulation itself),
    mirroring the exact "check under lock, compute unlocked, write
    under lock" pattern `api/main.py`'s own `_get_or_solve*` helpers
    already use — not inventing a new idiom, applying this codebase's
    own established one. Cheap because the locked sections are a single
    dict operation, not the surrounding computation.
  - **Gap 3, deliberately left unlocked, on the record, not silently
    skipped:** `InfoSetTable`'s `regret_sum`/`strategy_sum` read-
    modify-write updates are the single hottest path in the entire
    engine — touched once per node, per iteration, of *every* solve
    that exists today. Unlike gap 1 (real live risk) and gap 2 (cheap
    to harden), a lock here would cost every current, single-threaded
    caller real overhead for a scaling move (parallel traversers) that
    doesn't exist yet, whose actual synchronization needs aren't even
    decided — a future design might use per-thread `node_data` dicts
    merged at the end instead of per-table locking, an entirely
    different mechanism this milestone would have guessed wrong.
    Documented explicitly in `InfoSetTable`'s own class docstring
    instead: a decision on record, not a gap nobody noticed.
  - **Tests:** `tests/test_equity.py` gained a dedicated concurrency
    section — many threads racing the *identical* opponent tuple
    against `MultiwayEquityCache` (no crash, every thread's own result
    bit-identical given determinism, cache ends up with exactly one
    entry); many threads racing *different* opponent tuples (each gets
    its own correct value, cache ends up with exactly the right count
    of distinct entries); and the direct regression test for gap 1 —
    many threads hitting `get_equity_table` on a genuinely nonexistent
    cache path simultaneously, asserting every thread gets back a
    validly-shaped table, no leftover temp files, and the file actually
    left on disk loads cleanly and matches what every thread received.
  - **Verification:** `python -m pytest tests/ -v` — full suite, zero
    regressions (this touches `equity.py` again, the same file M27/M33
    already taught this project to re-verify broadly, not just re-run
    its own test file). `npm test` (frontend) — zero regressions
    (engine-only change, no frontend files touched).
  - **This closes recommendation #7 in full** (suit-assignment bias
    and the determinism-claim fix landed in M33; the three thread-
    safety gaps close it here) — the last item on `docs/full-table-
    diagnostic-2026-08.md`'s own prioritized recommendation list.
    Recommendations #1-#7 are now all addressed, each recorded in its
    own milestone entry above (M27-M34), including the places this
    session's own investigation corrected or refined the diagnostic's
    original framing rather than applying it verbatim (M32's finding
    that `postflop_action_order` — introduced at M29, after the
    diagnostic's own snapshot — would be misapplied by the tempting-
    but-wrong approach to §3.7's fix, and this milestone's own finding
    that only one of §3.10's three named thread-safety gaps was
    actually live today, not all three equally).

- **M35 — Connect `derive_ranges_from_path` to postflop combos: a real
  N-position multiway flop solve.** M30-M32 built the full engine
  machinery for true multiway postflop MCCFR solving but shipped it
  engine-only, zero real callers — confirmed by direct search before
  starting this milestone: `poker_solver/solver.py` had zero references
  to `NwayBoardEquityCache`, `build_mccfr_chance_branch`, or
  `SampledChanceBranch`. Separately, `derive_ranges_from_path` (M15/M16)
  already produces exactly the per-position, N-general range data this
  machinery needs, and `combos.range_from_class_frequencies` (M10)
  already converts one position's class-frequency dict into a
  board-legal combo-weight dict — the bridge *pattern* between these two
  was already proven, just at N=2 (`solve_flop`'s own pipeline test).
  **What was actually missing:** a solver.py-level function that runs a
  real multiway MCCFR solve given N positions' worth of already-combo-
  level ranges — the direct N-position generalization of `solve_flop`.
  Once that exists, "connecting" `derive_ranges_from_path` to it needs
  no new bridge *code* at all — the existing per-position `range_from_
  class_frequencies` composition, already proven at N=2, generalizes to
  N=3+ via a dict comprehension over `path_scenario.live_positions`
  instead of two named variables.
  - **New `solve_flop_multiway(board, position_ranges, pot, effective_
    stack_bb, positions, ...)`** — reuses `solve_flop`'s exact combo-
    pool-union pattern (generalized from two named `hero_range`/
    `villain_range` params to a `position: {HandCombo: weight}` dict),
    `mccfr_solve` + `NwayBoardEquityCache` (M30-M32) instead of `solve`
    + `build_board_equity_table`. No `nan_to_num` call needed at this
    layer, unlike `solve_flop` — `_mccfr_terminal_value` (M32) already
    applies it internally, one layer deeper, so this function's body
    stays a pure assembly step. `positions`/`position_ranges` must cover
    exactly the same positions, checked with an explicit `ValueError`
    (a mistyped position label would otherwise surface as a confusing
    downstream `KeyError` instead).
  - **The connection itself is a proven pipeline, not a new production
    function** — deliberately mirrors `solve_flop`'s own precedent (no
    dedicated "derive-and-solve" wrapper exists for it either), not
    `query_strategy_from_path`'s (which exists specifically because
    `query_strategy` has its own canonicalization/caching concerns this
    function doesn't share): `derive_ranges_from_path` → per position,
    `combos.range_from_class_frequencies` → `solve_flop_multiway`,
    proven by a real end-to-end test generalizing the existing N=2
    pipeline test.
  - **A real subtlety, caught by tracing the actual code rather than
    assumed:** `PathScenario.live_positions` stays in *preflop* acting
    order (`derive_ranges_from_path`'s own plain filter over `result.
    config.positions`) — it does not convert to postflop order itself.
    A caller must apply `game_tree.postflop_action_order` (M29) to get
    the correct N-position postflop seating order before calling
    `solve_flop_multiway` — the exact same step `query_strategy_from_
    path` (M23) already takes, now applied at N>=3 instead of being
    restricted to exactly 2. `solve_flop_multiway` itself takes
    `positions` as an explicit, caller-supplied parameter rather than
    trying to derive it internally, so it stays agnostic to where the
    order came from.
  - **Stack/pot derivation N-generalizes M23's own proven guard, rather
    than reinventing one:** requires `isinstance(path_scenario.node,
    TerminalNode)` — M23's own proof that this guarantees equal
    remaining stacks across every live position was already N-general
    in its underlying argument (`game_tree._build`'s no-side-pots
    invariant never mentions "2" anywhere); only its application was
    narrow, because its own consumer (`solve_flop`) was 2-position.
    Confirmed directly in the new pipeline test: `len({scenario.
    stacks[p] for p in scenario.live_positions}) == 1` — every live
    position's own remaining stack really does match at a genuine
    3-position terminal, exactly as the N-general proof predicts.
  - **A real, measured cost finding from this milestone's own scoping
    pass, not assumed — and the reason this ships flop-only, no
    chance dispatch:** flop-only multiway MCCFR (no turn-chaining at
    all) is expensive even at a modest combo pool. A 9-combo pool (3
    per position), `max_raises=1`, 30 iterations: **0.34s**. A 24-combo
    pool (8 per position), the same tiny tree, only 50 iterations:
    **exceeded 100s** (killed rather than waited out). Pool size is the
    dominant cost driver — consistent with `multiway_board_equity.py`'s
    own O(pool_size) finding (M30), compounded here by cache-miss rate
    across the many distinct opponent tuples MCCFR samples iteration to
    iteration (a bigger pool means a bigger space of distinct tuples,
    so more expensive cache misses more often). `DEFAULT_FLOP_MULTIWAY_
    ITERATIONS = 200` mirrors `DEFAULT_FLOP_TURN_ITERATIONS`'s own
    "prove it small first" default (M14) — explicitly documented as not
    validated at realistic combo-pool scale, the same honesty precedent
    M9's own iteration-budget comments already established.
  - **A real directional-assumption correction, measured before
    shipping, mirroring M15/M16's own "AA prefers raising over calling"
    finding resurfacing a third time:** the new pipeline test's first
    draft asserted AA continues more than trash at all 3 positions along
    a real "BTN opens, SB calls, BB calls" path — measured instead:
    AA's own weight at BB (facing the open, deciding whether to call)
    is a tiny 0.0004, *smaller* than trash's own 0.305, for the exact
    reason M15 already documented (AA prefers 3-betting/jamming over
    flat-calling). KK — a real flatting/opening hand at all 3 positions
    in this pool, measured at 0.69-0.85 vs. trash's 0.0017-0.305 — is
    the comparator that actually behaves the way naive "premium
    continues more" intuition expects, applied here rather than
    re-discovering the fix a third time.
  - **Tests:** `tests/test_solver.py` gained a full `solve_flop_
    multiway` section mirroring `solve_flop`'s own exactly (combo-pool
    union, frequencies-sum-to-one, a real-hand-vs-air facing-a-bet
    directional check — now at MID, the second actor at a genuine
    3-position table, a node a 2-position test structurally can't
    reach — config/root sanity, determinism given a seed, default-
    iterations fallback, a missing-combo-gets-zero-weight check, the
    `positions`/`position_ranges` mismatch `ValueError`), plus the
    required end-to-end pipeline test described above, reusing the
    existing `three_max_result` fixture (no new slow preflop solve).
  - **Verification:** `python -m pytest tests/ -v` — 630 passed, zero
    regressions (up from M34's 620), and notably *faster* than the
    M33/M34 runs (387.54s vs. 734-786s) — consistent with those
    earlier runs' own honestly-reported conclusion that the timing
    variance was ordinary machine-load noise, not a real regression, now
    further corroborated by a third data point. `npm test` (frontend) —
    139 passed, zero regressions (engine-only change).
  - **Scope:** engine only, no `api/main.py`/frontend changes, no
    chance-dispatch (turn-chaining) variant, no live endpoint — matches
    M30-M32's own "engine machinery first, live wiring is separate,
    measured-later work" precedent, now reinforced by this milestone's
    own real cost finding (flop-only multiway MCCFR is *already*
    expensive at modest scale, before chance dispatch even enters the
    picture). A `solve_flop_turn_multiway`-shaped follow-on (reusing
    M32's `build_mccfr_chance_branch` machinery) and a live endpoint are
    natural next milestones, each needing their own dedicated cost
    measurement before scoping further — not attempted here.

- **M36 — `solve_flop_turn_multiway`: chaining a multiway flop into a
  real multiway turn decision.** The direct N-position generalization
  of `solve_flop_turn` (M12), mirroring `solve_flop_multiway`'s own
  relationship to `solve_flop` — `mccfr_solve`'s `board`/`chance_fn`/
  `chance_data` params (M32) wired to `chance.build_mccfr_chance_branch`
  instead of `solve_flop_multiway`'s "average every remaining runout
  inside `NwayBoardEquityCache` itself" shortcut.
  - **A real, encouraging cost finding from this milestone's own
    scoping pass, measured directly against M35's own numbers at
    matching configs — the opposite of the "chance dispatch adds real
    cost" assumption a naive read of M12's own history would predict:**
    a 9-combo pool (3/position), `max_raises=1`, 30 iterations — the
    exact config M35 measured at 0.34s flop-only — costs **0.38s** with
    chance dispatch active, only ~12% more. Traced, not just observed:
    each dispatched branch's own `NwayBoardEquityCache` is scoped to a
    4-card (turn) board, resolved *exactly* (`remaining_needed==1`, per
    `multiway_board_equity.py`'s own optimization) rather than the
    flop-level board's `remaining_needed==2` Monte Carlo averaging — so
    chance dispatch mostly *replaces* one expensive Monte Carlo lookup
    with several cheaper exact ones, not stacks new cost on top of an
    unavoidable one. **That relief doesn't eliminate the real
    bottleneck, though — confirmed directly, not assumed away:** a
    24-combo pool (8/position), the exact config M35 measured exceeding
    100s flop-only, exceeded 120s here too (killed, not waited out).
    Pool size remains the dominant cost driver regardless of dispatch —
    `DEFAULT_FLOP_TURN_MULTIWAY_ITERATIONS = 50` (below `solve_flop_
    multiway`'s own 200) reflects that dispatch's extra per-(terminal,
    card) construction cost is real even though smaller than expected.
  - `StrategyResult.chance_data`'s own shape differs from `solve_flop_
    turn`'s, documented explicitly rather than left as a surprise: keyed
    by `(id(terminal), card)` — M32's own per-sampled-card memoization
    — not one `chance.ChanceNode` per terminal the way the exact
    solver's `chance_data` is, since `mccfr_solve` only ever builds the
    ONE branch actually sampled that iteration, never all ~49 possible
    next cards.
  - **Tests:** `tests/test_solver.py` gained a full `solve_flop_turn_
    multiway` section mirroring `solve_flop_turn`'s own M12 tests
    (deliberately the smallest possible combo pool — 1 combo per
    position, matching `solve_flop_turn`'s own precedent, not M35's
    slightly larger 2-per-position fixture, since chance dispatch's own
    extra cost per (terminal, card) pair still adds up even at a modest
    3-combo pool): union of all three ranges, frequencies-sum-to-one,
    root sanity, the direct proof chaining actually happened (`chance_
    data` non-empty, at least one branch reaches a real turn-street
    `DecisionNode`, that node's own strategy is well-formed), determinism
    given a seed, default-iterations fallback, the `positions`/
    `position_ranges` mismatch `ValueError`.
  - **Verification:** `python -m pytest tests/ -v` — 637 passed, zero
    regressions (up from M35's 630). `npm test` (frontend) — 139 passed,
    zero regressions (engine-only change).
  - **Scope:** engine only, no `api/main.py`/frontend changes, no
    turn->river chaining (a second hop — `chance.build_mccfr_chance_
    branch`'s own one-hop-only M32 scope carries through unchanged), no
    live endpoint. A live endpoint remains the one natural next
    milestone this thread hasn't closed — needing its own cost-vs-
    request-latency scoping decision (mirroring M14's own "ship both
    endpoints, accept the real measured cost" precedent, or a curated-
    demo-pool-sized-to-be-fast approach instead), not attempted here.

- **M37 — `GET /solve_flop_multiway` and `GET /solve_flop_turn_multiway`:
  the first live endpoints for true multiway postflop solving.** Every
  prior postflop endpoint in this project, however deep the runout, has
  been 2-position (OOP/IP) end to end — this wires M35's `solve_flop_
  multiway` and M36's `solve_flop_turn_multiway` into `api/main.py` for
  the first time, mirroring M14's own "ship both a flop-only and a
  chained-to-turn endpoint together" precedent, now for 3+ live
  positions.
  - **Both response shapes reused completely unchanged, confirmed by
    tracing the code rather than assumed:** `FlopSolveResponse`/`format_
    flop_response` were already position-count-agnostic — every field
    they read (`result.config.pot`/`.stack_bb`, `result.root.player_to_
    act`, `strategy_for_position`/`trained_for_position`, `list(result.
    config.positions)`) is already N-general on `StrategyResult`.
    `strategy_format.py`'s own docstring had explicitly anticipated this
    exact gap since M14 ("multiway postflop solving is this project's
    own named, still-unscoped next structural gap... whenever it lands,
    this field is already exactly where it needs to be") — it landed
    here, unmodified. `position` now simply accepts `OOP`, `MID`, or
    `IP`; the response's own `positions` field carries all 3.
  - **A curated 3-max-only demo pool** (`DEMO_MULTIWAY_FLOP_CLASSES` —
    one suited class per position, 11 combos total after board-legal
    expansion), deliberately not 6-max/9-max — M35/M36 both measured
    pool size as the dominant cost driver for this whole solving path,
    and neither table size's own postflop cost has ever been measured;
    3-max-first here mirrors M8/M9's own precedent for multiway
    *preflop* before 6-max/9-max became their own later milestones.
  - **A real, encouraging cost finding, carrying M36's own finding
    forward into live-endpoint terms:** measured live, at this pool:
    `solve_flop_multiway` costs ~3.0-3.5s, close to flat across
    iteration count (200 vs 2000 iterations: ~3.0s vs ~3.5s — the
    equity cache saturates fast at this small a pool, mirroring `/solve_
    flop_turn`'s own identical "flat cost" shape and reasoning) — so
    `MAX_FLOP_MULTIWAY_ITERATIONS` gets the same generous 10x-default
    headroom `/solve_flop_turn`'s own cap does. `solve_flop_turn_
    multiway` costs ~1.3-13.8s depending on iteration count (50 iters
    ~1.3s, 200 iters ~5.8s, 500 iters ~13.8s) — genuinely *not* flat,
    unlike its 2-position cousin: every iteration can sample a new
    `(terminal, card)` pair, a materially bigger space at this pool size
    than the flop-level equity cache's own opponent-tuple space — so
    `MAX_FLOP_TURN_MULTIWAY_ITERATIONS` is set far more conservatively
    (500, landing at the cap's own ~13.8s), the same "slow but tolerable
    for a live request" bracket `/solve_flop_to_river` was already
    accepted in at M14.
  - **Neither endpoint is pre-warmed** — both are cheaper than
    `/solve_flop`'s own already-"not worth pre-warming" ~2.6s at their
    respective defaults, so a cold-start tax was never the concern
    `/solve_flop_turn`'s/`/solve_flop_to_river`'s own pre-warming exists
    to avoid.
  - `_get_or_solve_flop_multiway`/`_get_or_solve_flop_turn_multiway` and
    their own `_flop_multiway_cache`/`_flop_turn_multiway_cache` +
    locks mirror `_get_or_solve_flop_turn`/`_get_or_solve_flop_to_river`'s
    exact shape — deliberately separate dicts per endpoint (not shared),
    the same "an identical key could otherwise collide between two
    endpoints with different `max_raises`" reasoning every prior pair of
    `/solve_flop*` endpoints already established.
  - **Tests:** `tests/test_api.py` gained a full section mirroring the
    M14 section's own structure (200-and-cached-across-positions, board/
    pot/stack validation, iterations-above-cap rejection, and a direct
    regression test that the two new endpoints' caches don't collide).
    The autouse pool-shrinking fixture gained a matching `DEMO_MULTIWAY_
    FLOP_CLASSES` shrink (one small suited class per position, `MULTIWAY_
    FLOP_MAX_RAISES` down to 1) — the same "shrink to the floor, test
    plumbing not convergence" idiom the existing fixture already applies
    to every other `/solve_flop*` endpoint's own demo pool.
  - **Verification:** `python -m pytest tests/ -v` — 646 passed, zero
    regressions (up from M36's 637). `npm test` (frontend) — 139 passed,
    zero regressions.
  - **Scope:** API only, no frontend changes — a real, nontrivial design
    question (traced, not assumed: neither `FlopSolver.tsx`'s own 2-
    position depth-selector nor `TableModeControl`'s own preflop-only,
    N-position selector directly fits a "3-position postflop depth
    selector" need — a genuinely new component, not a small extension of
    either existing one) deliberately left for its own follow-up
    milestone, matching this session's own established "engine, then
    API, then frontend" three-step pattern (M30-M32 engine-only, M35-M36
    engine-only, this milestone API-only).

- **M38 — Frontend for the multiway flop endpoints: `MultiwayFlopSolver.
  tsx`.** Closes the "engine, then API, then frontend" three-step arc
  M37 named as its own remaining piece — the first UI in this project
  for a genuinely 3+ live position postflop solve.
  - **A new, separate component, not a 3rd position bolted onto
    `FlopSolver.tsx`** — mirrors `CachedFlopSolver`'s own M22 "different
    interaction shape -> separate component" precedent exactly, traced
    (not assumed) before building: neither `FlopSolver.tsx`'s own
    2-option position `<select>` nor `TableModeControl`'s own preflop-
    seat/table-size selector fit a fixed 3-option (OOP/MID/IP) postflop
    selector paired with a *narrower* (2-, not 3-entry) depth selector
    — `TableModeControl` in particular is a table-size + preflop-seat
    component (`BTN`/`SB`/`BB` at 3-max), a different concept entirely
    from these endpoints' fixed `OOP`/`MID`/`IP` postflop roles.
  - **Everything else reused as-is, not reinvented:** the actual form/
    result markup, CSS classes (`.flop-solver`, `.detail-row`, `.bar-
    fill`, `.trained-indicator.untrained`), and `colors.ts`'s `gradient
    For`/`sortedEntries` helpers are copied from `FlopSolver.tsx`
    verbatim — the interaction *shape* differs (3 positions, 2 depths),
    the actual rendering doesn't need to. New `MultiwayFlopSolveDepth`
    type (`'flop' | 'flop_turn'`, deliberately not `FlopSolveDepth`
    reused/widened — no multiway analog of `'flop_to_river'` exists) and
    `fetchMultiwayFlopStrategy` + `MULTIWAY_FLOP_DEPTH_ENDPOINTS` in
    `api.ts`, mirroring `fetchFlopStrategy`/`FLOP_DEPTH_ENDPOINTS`'s
    exact shape. No `vite.config.ts` proxy change needed — both new
    routes start with `/solve`, already covered by the existing prefix
    entry (confirmed by tracing Vite's own prefix-match proxy behavior,
    not assumed — the exact class of gap M10's own `/equity` bug and
    M25's own `/preflop_walk` entry both had to fix for routes that
    *didn't* start with `/solve`).
  - **Hint copy grounded in M37's own real measured numbers**, not
    copied from `FlopSolver.tsx`'s unrelated ones: "typically a few
    seconds" for flop-only (M37 measured ~3.0-3.5s), "up to about 15
    seconds" for flop+turn (M37 measured up to ~13.8s at its own
    iteration cap) — deliberately different phrasing from `FlopSolver.
    tsx`'s own "up to about a minute"/"two minutes" copy, since these
    two endpoints are genuinely cheaper.
  - **Verified live, end to end, through the real browser and a real
    API server** (not just the mocked unit tests) — both `preview_start`
    servers running together (`frontend-dev` proxying to `api-dev`):
    a real flop-only solve returned 11 combos (4 AKs + 3 QJs, one QJs
    combo correctly blocked by the board's own `Jh` + 4 T9s — matching
    M37's own CLI-measured pool size exactly), OOP's own combos showing
    a real trained strategy while MID's/IP's own combos correctly show
    "low data" when viewing OOP's strategy (M28's own documented "zero
    reach weight -> untrained" pattern, now visible live in this UI for
    the first time); switching to MID reused the identical cached
    `elapsed_seconds` while MID's own combos flipped to a real trained
    strategy and AKs/T9s correctly went to "low data" instead; switching
    depth to "Flop + turn" correctly relabeled the button/hint, cleared
    the stale result, and a real chance-dispatched solve returned "50
    iterations, 1.35s" — matching `DEFAULT_FLOP_TURN_MULTIWAY_
    ITERATIONS`'s own measured default exactly; a malformed 2-card board
    correctly surfaced the real server-side 422 error message and
    cleared the stale result.
  - **Tests:** `MultiwayFlopSolver.test.tsx` (new, 10 tests) mirrors
    `FlopSolver.test.tsx`'s own mocking convention (raw `fetch`
    stubbing via `vi.stubGlobal`, not module mocking) — default-inputs
    rendering (including the 3-option position list and the *absence*
    of a flop-to-river depth option), solving and calling the correct
    endpoint per depth, a chosen `MID` position reaching the query
    string correctly, depth-appropriate button/loading copy, clearing a
    stale result on depth change, the untrained-indicator pill, error
    handling, and the 3-position status line.
  - **Verification:** `npm test` — 149 passed (up from M37's 139),
    zero regressions. `npm run build` (`tsc -b && vite build`) — clean,
    no type errors. `npm run lint` — clean. No backend files touched
    this milestone, so the (unaffected) Python suite wasn't re-run —
    the live end-to-end browser verification above exercises the real
    API server directly, which is the more direct evidence for a
    frontend-only change anyway.
  - **Scope:** frontend only. Closes the loop this session's own
    multiway-postflop thread opened at M30 — engine primitives (M30-
    M32), the bridge from real derived ranges (M35), turn-chaining
    (M36), a live endpoint (M37), and now a real UI (M38) — the first
    complete engine-to-UI path for true multiway (3+ live position)
    postflop solving in this project.

- **M39 — `solve_flop_to_river_multiway`: a second chance-branch hop,
  chaining all the way to a real multiway river showdown.** The direct
  N-position generalization of `solve_flop_to_river` (M13) — `chance.
  build_mccfr_chance_branch` gains a `chain_to_river` flag mirroring
  `build_chance_node`'s own identical M13 parameter/semantics exactly,
  and `solve_flop_to_river_multiway` mirrors `solve_flop_turn_multiway`'s
  own shape with that one flag added to its `chance_fn` closure.
  - **A real simplification found while implementing, not assumed
    going in:** `build_chance_node`'s own M13 closure needed the
    `_b=next_board, _s=remaining_stack` default-argument trick
    specifically because it builds *many* branches in one shared loop
    (all ~44-49 undealt cards), so without it every branch's closure
    would silently capture the *last* iteration's loop variables by
    reference. `build_mccfr_chance_branch` builds exactly *one* branch
    per call — `next_board`/`remaining_stack`/etc. are already that
    call's own locals, not shared loop state — so the entire bug class
    that trick exists to prevent structurally cannot occur here. No
    equivalent guard was needed, and none was added.
  - **A real, measured surprise — the opposite of M13's own finding for
    the exact 2-position solver:** M13 measured the second hop as
    *dramatically more* expensive than the first (`solve_flop_to_river`
    ~63-105s vs. `solve_flop_turn`'s own ~18-26s, at a matching 12-combo
    pool). Measured here, at the matching 11-combo pool M37's own live
    endpoint uses: `solve_flop_to_river_multiway` is *cheaper* than
    `solve_flop_turn_multiway` at every iteration count compared (200
    iters: ~3.89s vs. ~5.8s; every point from 20 to 500 iterations
    scales consistently linearly at ~0.019s/iteration, the cheapest
    number being ~0.45s at 20 iterations and the most expensive ~9.54s
    at 500). Traced, not just observed, to two independent reasons:
    (1) `build_chance_node`'s own eager, all-branches-at-every-level
    design pays a genuine combinatorial cost for a second hop (~44x49
    equity-table builds for one flop terminal, roughly the ratio
    M13's own numbers reflect) that `build_mccfr_chance_branch`'s lazy,
    one-sampled-card-at-a-time design simply never incurs — each
    iteration pays for at most one new turn dispatch and one new river
    dispatch, never a product of both levels' full branch counts;
    (2) a river-level equity lookup (`remaining_needed==0`, a complete
    5-card board) needs no enumeration at all, cheaper still than a
    turn-level lookup's own already-cheap `remaining_needed==1` exact
    enumeration. `DEFAULT_FLOP_TO_RIVER_MULTIWAY_ITERATIONS` is set to
    `solve_flop_turn_multiway`'s own default/cap (50/500) rather than
    `solve_flop_to_river`'s own tiny 2-position ones (20/=default,
    zero headroom) — the cost profile that justified those numbers
    doesn't hold here.
  - **`chance_data`'s own shape confirmed to compose across both hops
    without any `cfr.py` changes**, mirroring M13's own "no cfr.py
    changes needed" finding exactly, verified rather than assumed: a
    real solve's `chance_data` naturally contains entries whose own
    `board` field is a complete 5-card river even at a tiny 3-combo
    pool and only 30 iterations (confirmed directly, not just reasoned
    about: 20 of 56 total entries were river-level) — `_mccfr_recurse`
    already threads `branch.chance_fn` (never the ambient one) into
    every recursive call regardless of how many levels deep that
    branch's own `chance_fn` itself came from, so a turn-level branch's
    own populated `chance_fn` (from `chain_to_river`) gets used
    correctly with zero new dispatch logic.
  - **Tests:** `tests/test_chance.py` gained a `chain_to_river` section
    mirroring `build_chance_node`'s own M13 tests structurally (defaults
    to `False`; populates `chance_fn` when real stack remains; never
    populates it for an all-in-already-reused branch (the same
    structural, not incidental, guard `build_chance_node` established);
    never populates it once the board is already complete; deterministic
    across calls) plus the one genuinely new test the "no loop, no
    shared state" finding above makes possible and necessary — a direct
    two-hop invocation reaching a real 5-card board. `tests/test_solver.
    py` gained a `solve_flop_to_river_multiway` section mirroring
    `solve_flop_turn_multiway`'s own (same tiny 3-combo fixture, not a
    further-shrunk one — this milestone's own cost finding means there
    was no need to shrink further), plus the direct proof the second hop
    happened for real: a naturally-reached (not hand-constructed) branch
    whose own `board` field has 5 cards, with a well-formed strategy at
    its own real river decision.
  - **Verification:** `python -m pytest tests/ -v` — 659 passed, zero
    regressions (up from M38's 646 — this milestone touched no frontend
    files, so the frontend suite wasn't re-run; M38's own 149-pass
    result stands unaffected). No frontend/`api/main.py` changes.
  - **Scope:** engine only — matches this session's own established
    "engine, then API, then frontend" three-step pattern (M30-M32,
    M35-M36-now-M39 engine-only; M37 API; M38 frontend). A live
    `GET /solve_flop_to_river_multiway` endpoint and its own frontend
    depth option are the natural next milestones this one leaves open,
    each a small, low-risk addition given this milestone's own cost
    finding already de-risked the "is this affordable for a live
    request" question M13's own 2-position finding had left as a real
    concern for the analogous endpoint.

- **M40 — live `GET /solve_flop_to_river_multiway`, closing the endpoint
  half of M39's own deferred follow-on.** Mirrors M37's own two-endpoint
  pattern exactly: own cache dict/lock (`_flop_to_river_multiway_cache`/
  `_flop_to_river_multiway_lock`, same "collision-unsafe if shared"
  reasoning as every other `/solve_flop*` cache), own `_get_or_solve_
  flop_to_river_multiway` helper (identical shape to `_get_or_solve_
  flop_turn_multiway`, just calling `solve_flop_to_river_multiway`
  instead), same `DEMO_MULTIWAY_FLOP_CLASSES`/board/pot/raise-sizing
  menu the other two multiway endpoints already share (a runout-depth
  comparison needs the same underlying matchup at every depth, the same
  reasoning `DEMO_CHAINED_FLOP_HERO_/VILLAIN_CLASSES` already
  established for the 2-position endpoint trio).
  - **The one real decision this milestone made, and it was already
    answered before any code was written:** `MAX_FLOP_TO_RIVER_
    MULTIWAY_ITERATIONS`. M39's own engine-level measurement (at the
    identical 11-combo pool this endpoint's demo range produces) already
    showed `solve_flop_to_river_multiway` is *cheaper* than `solve_flop_
    turn_multiway` at every iteration count compared, the opposite of
    what the 2-position `solve_flop_to_river`/`solve_flop_turn` pair
    found — so, unlike M14's own analogous moment (where `solve_flop_to_
    river`'s materially worse cost curve forced a *stricter*, zero-
    headroom cap relative to `solve_flop_turn`'s), this endpoint's cap
    was set equal to `solve_flop_turn_multiway`'s own default/cap
    (50/500) with no new measurement needed — M39 had already done the
    measuring. Confirmed, not just inherited on faith: this endpoint's
    own request-level tests exercise the real cap path
    (`test_solve_flop_to_river_multiway_rejects_iterations_above_the_
    cap`), same as every sibling endpoint's own test.
  - **Frontend/docstring scope:** deliberately none this milestone — the
    module docstring documents the new endpoint (mirroring the existing
    `/solve_flop_multiway`/`/solve_flop_turn_multiway` paragraph's
    voice), but `MultiwayFlopSolver.tsx`'s own runout-depth selector
    stays at its current 2 options (Flop / Flop + turn). Adding a third
    ("Flop + turn + river") is the natural next milestone this one
    leaves open — the exact mirror of `FlopSolver.tsx`'s own 3-depth
    selector, now that the backend supports all three multiway depths
    the same way the 2-position family already did.
  - **Verification:** `python -m pytest tests/ -v` — 664 passed, zero
    regressions (up from M39's 659 — 5 new `test_api.py` tests: a happy-
    path/cross-position-cache test, a bad-board test, a bad-pot-or-stack
    test, an over-the-cap test, and a cache-independence test confirming
    a river-level `chance_data` entry — `len(branch.board) == 5` —
    actually appears through the real HTTP layer, not just at the
    engine level M39 already proved it at). No frontend files touched,
    so `npm test` wasn't re-run this milestone — matches M39's own
    identical "engine/API-only change, frontend suite untouched"
    reasoning.

- **M41 — a 3rd `MultiwayFlopSolver.tsx` runout-depth option
  ("Flop + turn + river"), closing the frontend half of M39's own
  deferred follow-on.** `MultiwayFlopSolveDepth` (`types.ts`) widens
  from `'flop' | 'flop_turn'` to include `'flop_to_river'`, kept as its
  own named type rather than folded into (or replaced by)
  `FlopSolveDepth` even though the three labels now match exactly — the
  two endpoint families' response shapes still differ in what
  `position`/`positions` can legitimately hold (3 positions vs. 2), the
  same reason the type was split out in the first place at M37.
  `MULTIWAY_FLOP_DEPTH_ENDPOINTS` (`api.ts`) gains a `flop_to_river`
  entry pointing at M40's new route.
  - **The one real content decision:** `DEPTH_CONFIG`'s new hint copy
    states the M39 finding directly ("measured cheaper than flop + turn
    alone") rather than a generic "up to N seconds" — the same kind of
    honest, specific copy `FlopSolver.tsx`'s own hints already carry
    (e.g. `solve_flop_to_river`'s hint calling out that it "varies more
    by board than the other two depths"), now extended to a case where
    the honest finding is a pleasant surprise instead of a caveat.
  - **Tests:** `MultiwayFlopSolver.test.tsx`'s existing "does not offer
    a flop-to-river depth option" test — a real M37-era regression
    guard, not dead weight — was flipped to its exact opposite
    assertion (the option is now expected, not absent), rather than
    deleted, so a future accidental removal of the option would still
    be caught. One new test mirrors the existing `flop_turn` case:
    selecting the depth, clicking the (correctly relabeled) button, and
    confirming the request lands on `/solve_flop_to_river_multiway`
    with the right query string.
  - **Verification:** `npm test` — 150 passed (up from 149 — net +1:
    one new test added, one existing test's assertion flipped in
    place, not counted as a second new test). `python -m pytest
    tests/ -v` not re-run — no backend files touched, mirroring M38's
    own "frontend-only change" precedent. Live-verified in the browser
    end to end (via `preview_start`'s `frontend-dev`/`api-dev` configs,
    working around the same known screenshot-tool limitation this
    session hit at M38 by driving the page with `javascript_tool`/
    `get_page_text`/`form_input`/`computer` clicks instead): selecting
    "Flop + turn + river" updates the button label and hint text
    live; clicking Solve issues a real request to
    `GET /solve_flop_to_river_multiway?board=Jh7d2c&pot=10&stack_bb=40&
    position=OOP` (confirmed 200 OK via `read_network_requests`); the
    rendered result shows a real strategy (50 iterations, 1.71s) with
    OOP's own trained AK-suited combos carrying real, differentiated
    frequencies and the untrained MID/IP-only combos correctly marked
    "low data."
  - **Scope, and what's now fully closed:** this closes out the entire
    M30-M41 multiway-postflop-solving arc's originally-scoped work —
    engine (M30-M32 board-aware N-way equity/range-seeding/chance-
    sampling primitives, M35-M36 flop/turn solving, M39 river
    chaining), API (M37 flop/turn endpoints, M40 river endpoint), and
    frontend (M38 component, M41 completing its depth selector) all
    now cover the same 3 runout depths the 2-position family has had
    since M14. True 6-max/9-max multiway *postflop* solving remains
    unscoped future work (the demo pool's cost is dominated by combo-
    pool size, measured in M35/M36 but never tested past 3-max), as
    does connecting `derive_ranges_from_path`'s own multiway output
    (proven at 3-max in M35's own pipeline test) into either live
    multiway endpoint the way M23/M24 did for the 2-position family.

- **M42 — `POST /solve_flop_multiway_from_path`: connecting
  `derive_ranges_from_path`'s multiway output into a live endpoint,**
  closing the gap M41's own entry named as still open. The multiway
  analog of `/solve_flop_from_path` (M24), for the case that endpoint
  structurally can't serve: a real action path leaving 3+ live
  positions at the flop, not just 2 — `query_strategy_from_path` (M23)
  is 2-position machinery all the way down (`query_strategy` ->
  `solve_flop` -> `build_board_equity_table`), so this endpoint calls
  `solve_flop_multiway` (M35) directly instead, behind its own plain,
  unpartitioned dict cache (no canonical-library collision risk to
  partition against, unlike `/solve_flop_from_path`'s own Finding 2).
  - **Scope boundary, deliberate, not a gap:** requires a genuine
    3+-live-position terminal; a 2-survivor path 422s with a message
    pointing at `/solve_flop_from_path` instead — that endpoint's exact,
    not MCCFR-approximate, 2-position solver is genuinely better for
    that case, not just a narrower option, so this endpoint doesn't try
    to also serve it.
  - **A real, load-bearing consequence of reusing `_get_or_solve_
    preflop_raw` unchanged, found before any cost measurement was
    needed:** whenever `players != 2`, that helper already ignores its
    own `iterations` argument and delegates to `_get_or_solve_multiway`,
    which solves over `MULTIWAY_TABLE_CONFIGS`' own small `DEMO_
    MULTIWAY_HANDS` pool (8 real classes) — not the full 169-class pool
    `/solve_flop_from_path` solves over at `players=2`. So this
    endpoint's own per-position class cap only ever ranks among 8
    classes, not 169 — M24's own Finding 1 (an uncapped 169-class pool
    costing hours) structurally cannot recur here, independent of
    whatever cap value gets chosen.
  - **Measured anyway, since `solve_flop_multiway`'s own cost curve is
    far steeper than `solve_flop`'s at any pool size** (M35's own
    finding: pool size is the dominant cost driver, compounded by
    MCCFR's opponent-sampling cache-miss rate): at a real 3-max
    open/call/call path reaching a genuine 3-live-position flop, at
    `solve_flop_multiway`'s own default (200) iterations —
    `MAX_MULTIWAY_PATH_QUERY_CLASSES_PER_POSITION=1` -> 18 combos,
    ~3.33s; `=2` -> 35 combos, ~22.46s; `=3` -> 62 combos, ~46.63s. Set
    to 2, landing in the same "tolerable for a live request" bracket
    `/solve_flop_from_path`'s own ~17-21s already established.
    Iteration-count scaling at that cap's own 35-combo pool is NOT
    close to flat, unlike `DEMO_MULTIWAY_FLOP_CLASSES`' own tiny
    11-combo pool: 200 iters ~22.46s, 500 iters ~36.76s, 1000 iters
    ~48.20s, 2000 iters ~58.13s — `MAX_MULTIWAY_PATH_QUERY_FLOP_
    ITERATIONS` is therefore set to 500 (~37s), not `solve_flop_
    multiway`'s own generous 2000-iteration ceiling (tuned against a
    much smaller pool).
  - **Reused, not rebuilt, at every step this session's own earlier
    milestones already proved general:** `derive_ranges_from_path`
    (M16) needed no changes — already N-position-general.
    `postflop_action_order` (M29) needed no changes — its own docstring
    had *already* anticipated this exact call shape ("Full, unfiltered
    output is still N-general on purpose, at zero extra cost, for
    whenever true multiway postflop solving... needs it"), confirmed
    true by using it here unmodified. `_cap_range` (M24) and
    `TerminalNode`-requires-equal-stacks (M23's own proven guard,
    already stated N-generally) both reused verbatim. The only
    genuinely new code this milestone wrote was the orchestration
    (`_query_flop_multiway_from_path`) and the request/response shapes
    — real evidence that M16/M23/M29's own "build it N-general even
    though nothing needs it yet" calls were the right ones.
  - **Response shape:** `FlopMultiwayPathQueryResponse` — no
    `canonical_board`/`canonical_stack_bb`/`hit` (no canonicalized
    library sits behind this endpoint); `positions` carries all of the
    path's real surviving positions (3+) in real postflop acting order;
    `flop_iterations` echoes what was actually used (unlike `/solve_
    flop_from_path`'s own fixed, unreported `PATH_QUERY_ITERATIONS` —
    this endpoint's flop-stage iteration count is real, request-
    controllable input, not a hidden constant, so it's worth confirming
    back).
  - **Known, deliberate gap, same as `_query_flop_from_path`/`_query_
    turn_from_path` before it:** `path_scenario.trained` isn't surfaced
    in this endpoint's response either — the same M29-named, still-open
    gap, not reopened or newly introduced here.
  - **Tests:** `tests/test_api.py` gained 8 new tests — a real 3-live
    line (BTN limps, SB calls, BB checks) returning 200 with a
    well-formed strategy; a 2-survivor path correctly rejected with a
    message pointing at `/solve_flop_from_path`; a non-terminal path; an
    unknown action kind; a malformed board; `flop_iterations` above the
    cap; a repeat query hitting the plain cache (identical
    `elapsed_seconds`); and two different `flop_iterations` values
    against the identical path producing two separate cache entries
    (proving the cache key isn't missing a real cost-affecting input).
  - **Verification:** `python -m pytest tests/ -v` — 672 passed, zero
    regressions (up from M41's 664 — 8 new tests, no other file's
    existing tests touched). No frontend changes this milestone (no
    frontend consumes this endpoint yet) — matches M24's own "engine/API
    only, frontend is a separate later milestone" precedent (M24 itself
    got its own frontend two milestones later, at M25).
  - **What's still open:** a frontend for this endpoint (mirroring
    M24-then-M25's own precedent — a curated-preset or general wizard
    UI is a separate, later decision); true 6-max/9-max multiway
    postflop solving (unscoped, since the preflop leg here is
    structurally capped to `DEMO_MULTIWAY_HANDS`' own 8 classes at any
    `players != 2`, this endpoint doesn't change that ceiling); and
    turn/river-depth versions of this same path-derived multiway
    endpoint (mirroring M26's own flop-then-turn precedent for the
    2-position family) — each a natural, separately-measurable next
    milestone, none attempted here.

- **M43 — a frontend for `/solve_flop_multiway_from_path`,** closing
  M42's own first-named open item — and, unlike M24-then-M25's own
  two-milestone gap (a curated-preset frontend first, a general wizard
  only later), this one goes straight to full wizard integration: since
  `ActionPathSolver.tsx`'s wizard (M25, extended to any table size by
  M29) already builds a real `action_path` one legal click at a time
  against any table size's own tree, a genuine 3+-live-position
  terminal was already reachable through the existing UI before this
  milestone — it just had nowhere correct to go (`handleSolve` always
  called `/solve_flop_from_path`, which 422s on exactly that case). This
  milestone fixes that live gap, not just adds a new feature.
  - **The routing fix:** `handleSolve` now branches on `walk.data.live_
    positions.length` — `>= 3` calls the new `fetchMultiwayFlopStrategy
    FromPath` (`/solve_flop_multiway_from_path`, M42) into its own
    `multiwaySolveResult` state; exactly `2` keeps calling the existing
    `fetchFlopStrategyFromPath` (`/solve_flop_from_path`) unchanged.
    Kept as two separate result states/render blocks, not one unioned
    type — `FlopMultiwayPathQueryResponse` and `FlopPathQueryResponse`
    genuinely differ (no `hit`/`canonical_board` on the multiway side, a
    3+-entry `positions` list instead of a fixed pair, a `trained` map
    the 2-position response doesn't carry) — mirrors this project's own
    "different response shape -> separate rendering, not a forced
    union" precedent (`MultiwayFlopSolver` vs. `FlopSolver`,
    `CachedFlopSolver` vs. `FlopSolver`). A new inline hint ("N live
    positions reached the flop — this calls /solve_flop_multiway_from_
    path, not the 2-position endpoint above") makes the routing visible
    to whoever's using the wizard, not just correct under the hood.
  - **The multiway result block reuses `MultiwayFlopSolver.tsx`'s own
    `trained`/`.trained-indicator.untrained` idiom** (M37/M28) — the
    first time this wizard's own rendering has needed to show untrained
    combos at all, since every prior result it showed came from the
    exact 2-position solver (`trained` always all-`True` there, per
    `format_flop_response`'s own docstring).
  - **Tests:** `frontend/src/api.test.ts` gained a `fetchMultiwayFlop
    StrategyFromPath` section (2 tests: a real POST body including
    `flop_iterations`, and a rejected request surfacing `SolveError`).
    `ActionPathSolver.test.tsx` gained one new end-to-end test: walk a
    real 3-max limp-call-check line to a genuine 3-live terminal,
    confirm the inline routing hint appears, click Solve, and confirm
    the request lands on `/solve_flop_multiway_from_path` (not
    `/solve_flop_from_path`) with the right body, rendering the
    multiway result block.
  - **Verification:** `npm test` — 153 passed (up from 150 — 2 new
    `api.test.ts` tests, 1 new `ActionPathSolver.test.tsx` test).
    `npx tsc --noEmit` clean. No backend files touched, so the Python
    suite wasn't re-run — matches M38/M41's own precedent. Live-verified
    end to end in the browser: switched to 3-max, walked BTN limps → SB
    calls → BB checks to a real terminal, confirmed the routing hint
    text, entered a board, clicked Solve, and confirmed (via `read_
    network_requests`) a real `POST /solve_flop_multiway_from_path ->
    200 OK`, rendering SB's real strategy (200 iterations, 36.96s,
    `SB/BB/BTN`) with trained AK-suited/KK combos carrying real
    differentiated frequencies and every other combo correctly marked
    "low data."
  - **What's still open:** M42's own remaining two items (true 6/9-max
    multiway postflop solving; turn/river-depth path-derived multiway
    endpoints) are both untouched by this frontend-only milestone —
    this closes the *frontend* gap specifically, not either of those
    separately-scoped engine/API questions.

- **M44 — `POST /solve_turn_multiway_from_path`: the multiway analog of
  `/solve_turn_from_path` (M26),** closing the "turn-depth" half of
  M42/M43's own remaining open item (the other half, true 6/9-max
  multiway postflop solving, stays untouched). Mirrors `_query_flop_
  multiway_from_path`'s own M42 scope boundary: requires a genuine
  3+-live-position terminal; a 2-survivor path 422s with a message
  pointing at `/solve_turn_from_path` instead. Uses `flop_iterations`,
  not a separate `turn_iterations` field — `solve_flop_turn_multiway`'s
  own single-solve design (M36) produces both the flop and turn
  strategies from ONE call, unlike the exact 2-position solver's own
  two-stage cost profile M26 named its own two iteration fields for.
  - **A real, structural gap this milestone had to solve that M26 never
    faced, found during design rather than discovered as a bug later:**
    `solve_flop_turn_multiway`'s own `chance_data` only ever contains
    the `(terminal, card)` pairs MCCFR actually happened to SAMPLE
    while solving (see that function's own docstring) — not every legal
    next card, the way the exact solver's `chance_data` does (`build_
    chance_node` eagerly builds all ~44-49 branches per terminal). A
    real turn card a client asks about can easily be one MCCFR never
    sampled, especially spread across a derived pool's many distinct
    terminals at a modest iteration budget — `_query_turn_from_path`'s
    own clean `if turn_card not in chance_node.branches: raise
    ValueError` (correct there, since the exact solver enumerates
    everything) would have made this endpoint frequently, frustratingly
    unusable for the exact real-derived-situation use case it exists
    to serve.
  - **Fixed with a new engine primitive, not a workaround in `api/
    main.py`:** `poker_solver.solver.ensure_flop_turn_multiway_branch` —
    on a `chance_data` miss for a legal card, builds and caches exactly
    the branch MCCFR would have built had it sampled that pair itself
    (`chance.build_mccfr_chance_branch` is a pure function of its
    inputs, proven deterministic in M32's own tests, so passing the
    identical `board`/`combos`/`positions`/`effective_stack_bb`/
    `raise_sizes`/`max_raises` reproduces exactly what real sampling
    would have built). Placed in `solver.py`, not called directly from
    `api/main.py` against `chance.py` — matches this project's own
    established layering (every endpoint orchestrates `solver.py`-level
    functions; `solver.py` is the layer that knows about `chance.py`/
    `cfr.py` internals), and makes the primitive independently testable
    and reusable outside this one endpoint.
  - **A real design question resolved by reading, not assuming:** does
    a freshly-built (MCCFR-untouched) turn node need special-casing to
    report low confidence? No — `StrategyResult.strategy_at`/`.trained_
    hands` already fall back to the untrained uniform default for ANY
    node absent from `node_data` (M28's own existing "no entry ->
    uniform + `trained=False`" behavior, confirmed by reading both
    methods' own docstrings, not assumed) — an unvisited node already
    behaves identically to a visited-but-untrained one. So the on-
    demand branch's own strategy needed zero new code to report
    honestly: it just IS an unvisited node, and the existing machinery
    already does the right thing.
  - **Measured, not assumed, before finalizing the class cap:** at the
    same real 3-max open/call/call path/board M42's own cap comment
    measured, chained into `solve_flop_turn_multiway` instead of `solve_
    flop_multiway`: cap=1 -> 18 combos, 50 iters ~4.19s / 200 iters
    ~16.17s; cap=2 -> 35 combos, 50 iters ~10.12s / 200 iters ~42.69s.
    Set `MAX_MULTIWAY_TURN_PATH_QUERY_CLASSES_PER_POSITION` to 2 — the
    SAME value as the flop-only endpoint's own cap, unlike M26's own
    precedent (where the turn-level cap had to SHRINK relative to the
    flop-level one, because the exact solver's own chance dispatch is
    expensive). `solve_flop_turn_multiway`'s chance dispatch is cheap
    enough at this pool size — the same lazy, one-sampled-card-at-a-
    time construction M36/M39 already found cheaper than expected — that
    the same class count stays affordable. Default kept at `solve_flop_
    turn_multiway`'s own default (50, ~10.12s at this pool); cap set to
    200 (~42.69s), landing in the same "slow but tolerable for a live
    request" bracket `/solve_turn_from_path`'s own ~46s already
    established.
  - Own cache dict (`_turn_multiway_path_cache`), keyed only on the
    preflop leg (not `flop_action_path`/`turn_card`, resolved by walking
    the already-solved tree afterward — the same "resolving is free,
    re-solving isn't" reasoning `_turn_path_cache`'s own M26 key already
    established). The SAME lock also guards `ensure_flop_turn_multiway_
    branch`'s own in-place `chance_data` mutation, preventing two
    concurrent requests from racing to build the same missing branch.
  - **Tests:** `tests/test_solver.py` gained a dedicated `ensure_flop_
    turn_multiway_branch` section (4 tests: returns the cached branch
    unchanged on a hit; builds and caches a genuinely new branch on a
    miss, with every hand correctly reporting `trained=False`; a second
    call against the same miss hits the now-populated cache without a
    duplicate build; raises for an illegal card). `tests/test_api.py`
    gained 10 tests, including the one that matters most: a real
    end-to-end HTTP-layer test that populates the cache with one real
    request, inspects the cached `StrategyResult`'s own `chance_data` to
    find a real, definitely-never-sampled card for the exact same flop
    terminal, requests exactly that card, and confirms a 200 response
    with every hand correctly marked `trained=False` — proving the
    on-demand fallback actually fires through the real endpoint, not
    just at the engine level.
  - **Verification:** `python -m pytest tests/ -v` — 686 passed, zero
    regressions (up from M43's 672 — 4 new `test_solver.py` tests, 10
    new `test_api.py` tests). No frontend changes this milestone —
    matches M42's own "engine/API first, frontend later" precedent (M42
    itself got its own frontend the very next milestone, at M43).
  - **What's still open:** a frontend for this endpoint (the natural
    next milestone, mirroring M42-then-M43's own precedent — wiring
    into `TurnPathSolver.tsx` the same way M43 wired `/solve_flop_
    multiway_from_path` into `ActionPathSolver.tsx`); true 6/9-max
    multiway postflop solving (still unscoped); and river-depth path-
    derived multiway advice one street further (mirroring M26's own
    "river-level advice... already de-risked cost-wise" note, now with
    an additional, real open design question this milestone's own
    on-demand-build fallback didn't have to answer yet: whether a
    SECOND chained hop's own chance_data needs the identical fallback
    treatment `ensure_flop_turn_multiway_branch` provides here, or a
    structurally different one).

- **M45 — a frontend for `/solve_turn_multiway_from_path`,** closing
  M44's own first-named open item, mirroring M42-then-M43's precedent
  (wire the new endpoint into the existing wizard the very next
  milestone, not deferred). `TurnPathSolver.tsx`'s wizard already let a
  user walk to a genuine 3-live-position flop at any 3+-max table
  (`usePreflopWalk` is already N-general, M25/M29) — before this
  milestone, clicking Solve there always called `/solve_turn_from_path`,
  which 422s on exactly that case. Same "fixes a live gap" framing M43
  used for its own analogous fix.
  - **The one real design decision, resolved by NOT hand-enumerating a
    second `FLOP_PRESETS`-shaped set:** `FLOP_PRESETS` is calibrated
    against the 2-position `solve_flop_turn` tree specifically (matching
    `FLOP_TURN_MAX_RAISES`/`RAISE_SIZES`'s own values) — a genuinely
    different, N-position-dependent set of legal terminal paths exists
    at 3+ live positions, and hand-curating one such set PER table size
    (3/6/9-max) the way M26 curated the 2-position one would be fragile
    and a real scope expansion. Instead, when `live_positions.length >=
    3`, the flop-line dropdown is replaced with the ONE flop line
    guaranteed structurally valid at any live-position count — everyone
    checks (`Array(liveCount).fill('call_or_check')`) — with an inline
    hint explaining the swap. A real general "what's legal on the flop
    from here" walker (already named by both M26's and M42/M43's own
    notes as the eventual fix for exactly this kind of curated-preset
    limitation) remains the natural, larger follow-up; not attempted
    here.
  - Two separate result states/render blocks (`solveResult`/
    `multiwaySolveResult`), same reasoning `ActionPathSolver.tsx`'s own
    M43 fix already established — the response shapes genuinely differ
    (`TurnMultiwayPathQueryResponse` has no `hit`, a 3+-entry `positions`
    list, `flop_iterations` echoed back). The multiway "already
    resolved" terminal message is deliberately simpler than the
    2-position branch's fold-vs-all-in distinction (`flopPathFoldsOut`)
    — the only multiway flop line this component ever submits (everyone
    checks) structurally can't fold or go all-in, so that distinction
    doesn't apply here.
  - **Tests:** `TurnPathSolver.test.tsx` gained one new end-to-end test
    (11 total, up from 10): switch to 3-max, walk BTN limps → SB calls
    → BB checks to a genuine 3-live terminal, confirm the "Flop line"
    dropdown is replaced by the routing hint, click Solve, and confirm
    the request lands on `/solve_turn_multiway_from_path` (not
    `/solve_turn_from_path`) with the everyone-checks flop line and the
    right body, rendering the multiway result block.
  - **Verification:** `npm test` — 154 passed (up from 153). `npx tsc
    --noEmit` and `npm run lint` both clean. No backend files touched.
    Live-verified end to end in the browser: walked a real 3-max
    limp-call-check line to a genuine terminal, confirmed the routing
    hint replaced the flop-line dropdown, entered a board and an
    arbitrary turn card, clicked Solve, and confirmed (via network
    inspection) a real `POST /solve_turn_multiway_from_path -> 200 OK`
    (50 iterations, 11.33s, `SB/BB/BTN`) — and, a genuinely useful extra
    confirmation neither planned nor scripted: the chosen turn card
    happened to be one MCCFR never sampled during the real 50-iteration
    solve, so EVERY combo rendered with the "low data" indicator,
    live-demonstrating M44's own on-demand-build fallback firing through
    the full UI, not just the test suite.
  - **What's still open:** true 6/9-max multiway postflop solving
    (unscoped); river-depth path-derived multiway advice (unscoped, with
    the open on-demand-fallback design question M44's own entry already
    named); and the general flop-action wizard both this milestone and
    M26 before it deferred rather than re-attempted.

- **M46 — `POST /solve_river_from_path`: real river-level advice,
  closing the last street this project's real-action-path thread had
  left uncovered.** Following the user's own explicit reprioritization
  (river coverage first, then solve speed, live-table integrations
  deferred — see the `v3-roadmap-priority` memory), this extends
  `/solve_turn_from_path` (M26) one hop further via `solve_flop_to_
  river` (M13) instead of `solve_flop_turn`. A real river decision
  needs a real TURN action path too (the turn is itself a full betting
  round) — unlike the turn endpoint's own deliberate "expose only the
  first turn decision" scope cut, which needed no such field — so the
  request grows a `turn_action_path` plus a real dealt `river_card`.
  Heads-up only this milestone, matching every other 2-position-first-
  multiway-later sequencing already established in this project (M12/
  M13 before M39, M26 before M44); multiway river-from-path is a likely
  follow-up, not attempted here.
  - **The real finding, measured before any cap was chosen:**
    `solve_flop_to_river`'s cost is dominated by combo-pool size far
    more steeply than any other path-derived endpoint in this file — so
    steeply that even a single CLASS-level cap (the lever every sibling
    endpoint uses) is already too coarse. Measured directly, same real
    preflop line/board/iterations throughout: capping to the single top
    class per side (up to 16 combos after expansion, since one offsuit
    class alone can be 12 combos) cost **224.43s** at this function's
    own already-tight default iteration count (20). Capping by raw
    COMBO count instead, at that same iteration count: 1 combo/side (2
    total) -> 14.10s; 2/side (4 total) -> 27.94s; 3/side (6 total) ->
    43.00s — a real, roughly linear ~7s/combo relationship, not the
    unpredictable jump a class-sized cap produces. `_cap_range_to_
    combos` (new) expands to real combos FIRST, then caps — the inverse
    order of every other path-derived endpoint's own `_cap_range`.
    `RIVER_PATH_QUERY_MAX_COMBOS_PER_SIDE` set to 3 (~43s), the same
    "slow but tolerable for a live request" bracket `/solve_flop_to_
    river`'s own fixed-demo endpoint was accepted in at M14 (~63-105s).
    `river_iterations`' own cap mirrors `MAX_FLOP_TO_RIVER_ITERATIONS`'
    own "==default, zero headroom" discipline — cost at this scale is
    already at the outer edge of tolerable at the default alone, so the
    field can only ever request a faster, noisier result.
  - **Structurally simpler than M44's own multiway-turn analog, for a
    real, verified reason:** `solve_flop_to_river` is the EXACT solver
    (`chance.build_chance_node` with `chain_to_river=True`), which
    exhaustively builds every reachable chance branch during solving —
    unlike MCCFR's own lazy, only-what-was-sampled `chance_data`. So
    every showdown-eligible turn-level terminal the client's own
    `turn_action_path` can resolve to is guaranteed already present in
    `chance_data` once `solve_flop_to_river` has run — no on-demand-
    build fallback (`ensure_flop_turn_multiway_branch`'s own M44
    machinery) is needed here at all. `chance_data` composes two levels
    deep with zero new `cfr.py` code (M13's own already-proven finding,
    confirmed still true rather than re-derived), so `_query_river_
    from_path` is a direct, mechanical one-hop extension of `_query_
    turn_from_path`'s own structure: resolve `flop_action_path` ->
    `chance_data[id(flop_node)]` -> deal `turn_card` -> resolve `turn_
    action_path` against the resulting turn root -> `chance_data[id(
    turn_node)]` -> deal `river_card` -> read whatever real strategy
    is already sitting there.
  - **A real correctness subtlety, verified against `StreetConfig`'s
    own per-street reset before trusting it, not assumed by analogy:**
    computing `remaining_stack` entering the river needed care — `turn_
    node.invested` (the TURN street's own fresh, 0-based investment
    tracking, per `game_tree.py`'s own `pot_offset` design) is NOT
    cumulative with the flop's own investment, so `remaining_stack_
    after_turn = remaining_stack_after_flop - max(turn_node.invested.
    values())`, mirroring `_query_turn_from_path`'s own identical-shape
    computation one level deeper, not a naive re-use of `effective_
    stack_bb` directly.
  - **Pre-warmed**, unlike every other path-derived endpoint in this
    file — its own cost (~43s at default) is a meaningfully worse cold-
    start tax than any of them, the same "worth pre-warming" bar `solve_
    flop_turn`/`solve_flop_to_river`'s own fixed-demo pre-warms were
    held to at M14.
  - **Tests:** `tests/test_api.py` gained 14 new tests, mirroring the
    turn-path section's own structure one hop deeper (a real non-uniform
    river strategy; cache reuse across river/turn cards and flop/turn
    action lines, including all four already-resolved terminal shapes —
    all-in-on-flop, folded-on-flop, all-in-on-turn, folded-on-turn;
    partition-by-preflop-leg; illegal/malformed river card; illegal turn
    action kind; non-terminal turn/flop/preflop paths; too-long turn
    action path; out-of-range `river_iterations`; a multiway origin
    narrowed to 2 survivors; a rejected 3-live-survivor multiway path;
    and players=2/3 not sharing a cache entry) — all 14 pass in ~37s at
    the fixture's own shrunk 1-combo-per-side cap, confirming the real
    cost driver is combo count, not test-suite-breaking by itself.
  - **Verification:** `python -m pytest tests/ -v` — 700 passed, zero
    regressions (up from M45's 686). No frontend changes this
    milestone — matches M24-before-M25/M42-before-M43/M44-before-M45's
    own "engine/API first" precedent; per the user's own stated
    priority (frontend is a secondary tool, not the focus), a river
    depth option for `TurnPathSolver.tsx`/`ActionPathSolver.tsx` is a
    natural, smaller follow-up, not required to consider river coverage
    itself complete.
  - **What's still open:** a frontend for this endpoint; multiway
    river-from-path (unscoped, cost not yet measured at that scale);
    and — per the user's own stated next priority — solve speed work
    generally, now that every street through the river has a real,
    working (if slow) path-derived answer.

- **M47 — first solve-speed pass: profile before guessing, ship the
  real (if modest) win, name the real (if bigger) one honestly.**
  Per the user's own stated priority (river coverage done at M46, solve
  speed next), this milestone starts the speed thread with a real
  `cProfile` run of `solve_flop_to_river` at M46's own real derived-
  range scale (3 combos/side, the production cap) — not a guess about
  where the cost lives.
  - **What profiling found:** `poker_solver.cards.remaining_deck`
    (called once per combo PAIR inside `board_equity.build_board_
    equity_table`'s own O(N²) loop — 73,062 calls at this scale) was
    rebuilding the *entire* 52-card deck from scratch on every single
    call, constructing up to two fresh `Card()` instances per candidate
    slot (one to test set membership, one to keep) — `Card.__post_
    init__`'s own validation work alone accounted for ~6.9M redundant
    calls in the profile. Fixed: `_ALL_CARDS`, all 52 `Card` objects
    built exactly once at module import (safe — `Card` is a frozen/
    immutable dataclass, so sharing the same objects across every
    caller can never cause a mutation bug) — `remaining_deck` now
    filters this precomputed list instead of reconstructing cards;
    `Deck.__init__` reuses the same list for the identical reason.
  - **The honest, measured result: real, but far smaller than the raw
    profile numbers suggested.** `cProfile`'s own cumulative-time
    figures made this look like a ~25-30% win (`remaining_deck`'s
    profiled cumulative time dropped from 16.9s to 2.2s) — but a real,
    unprofiled wall-clock timing comparison at the identical scale told
    a different story: ~43.0s (M46's own measurement) -> ~41.3s
    (average of two fresh runs) — only ~4% faster. Traced, not left
    unexplained: `cProfile` adds real per-call instrumentation overhead
    that compounds across millions of calls, so a function's *profiled*
    cumulative time can substantially overstate its *true* contribution
    when its call count is this extreme — a genuinely useful
    methodological lesson for any future profiling pass in this
    codebase, not just a footnote. The fix itself is still correct,
    real, and free (zero behavior change, `remaining_deck`'s own
    contract unchanged) — it's just not the big lever.
  - **A bigger idea, considered and correctly rejected before writing
    any code, not discovered as a bug later:** since `_query_river_
    from_path`'s own caller already knows the *specific* `turn_card`/
    `river_card` they want, could `chance.build_chance_node`'s own
    eager, all-~44-49-branches-at-every-level construction be made
    lazy — building only the one requested branch, mirroring `chance.
    build_mccfr_chance_branch`'s own lazy design (M32)? No — and the
    reason is load-bearing, not incidental: `cfr.solve()`'s own EXACT
    CFR+ recursion needs the true *expected value* across every
    possible next card to correctly compute the FLOP-level (and every
    intermediate) node's regret/strategy — skipping unrequested
    branches would silently turn an exact solve into a biased partial
    average, corrupting correctness at every street above the one the
    client asked about, not just saving unused work. This is
    fundamentally different from MCCFR's own chance-sampling, where
    sampling one card at a time *is* the algorithm's own unbiased-in-
    expectation strategy — the same "sampled vs. exact" distinction
    `EXPLORATION_EPSILON`'s own docstring already draws elsewhere in
    this codebase, now re-confirmed in a new context rather than
    re-learned the hard way by shipping something broken.
  - **The real remaining levers, named for whichever future milestone
    picks this thread back up, not attempted here:** profiling's own
    numbers point at `hand_eval.best_hand_rank_batch`/`_rank_five_batch`
    (real vectorized hand-strength computation, the genuine dominant
    cost — `best_hand_rank_batch` already evaluates all `C(7,5)=21`
    five-card sub-hands per 7-card hand, so this is correctness-
    necessary work, not accidental waste) as the true bottleneck. A
    materially faster hand evaluator (e.g., a precomputed lookup-table
    design, the "2+2 evaluator" pattern real production poker tools
    use, trading memory for O(1) lookups instead of O(21) combo
    enumeration + sorting) is the biggest real lever identified — but
    it's a substantial, standalone rewrite needing its own careful
    design and the same cross-validate-against-the-trusted-scalar-
    reference discipline `best_hand_rank_batch` itself was already held
    to at its own introduction, not a quick fix to bolt onto this
    milestone. A restructured two-phase solve (solve the flop+first-
    turn cheaply via `solve_flop_turn`, then a small, separate, on-
    demand solve of just the one requested turn subtree to the river)
    was also considered as a way to avoid paying for ~44x49 branches'
    worth of construction when a caller only ever reads out one path —
    real and promising in principle, but a genuinely new solve
    architecture, correctly scoped as its own future milestone rather
    than attempted inline here.
  - **Tests:** `tests/test_cards.py` gained 3 new tests — `_ALL_CARDS`
    has exactly 52 unique cards; `remaining_deck` returns the shared
    objects themselves (identity, not just equality — a real regression
    guard against silently reverting to fresh construction); and two
    separate `Deck()` instances share card objects but never share (or
    corrupt) each other's own list.
  - **Verification:** `python -m pytest tests/ -v` — 703 passed, zero
    regressions (up from M46's 700 — 3 new tests). No frontend changes.
  - **Scope:** a real, shipped, measured win — just an honestly modest
    one. The bigger levers (a faster hand evaluator, a restructured
    two-phase solve) are named, not attempted, pending direction on
    which one to pursue next.

- **M48 — the big speed lever: a prime-product lookup-table hand
  evaluator, replacing `_rank_five_batch`'s per-hand counting/masking/
  argsort pipeline.** The user chose this over the two levers M47 named
  (a faster hand evaluator vs. a restructured two-phase solve) —
  `hand_eval.best_hand_rank_batch`/`_rank_five_batch` was profiling's
  own confirmed dominant cost (~60% of `solve_flop_to_river`'s runtime),
  and this milestone attacks it directly with the same technique real
  production poker evaluators (the "Cactus Kev"/"Two Plus Two" family)
  use — adapted to stay fully vectorized across NumPy arrays, not a
  scalar-only port.
  - **The algorithm:** each of the 13 card values gets a distinct prime
    (2, 3, 5, ..., 41). A 5-card hand's VALUES multiply to a product
    that's unique per *value multiset* (order-independent) by the
    fundamental theorem of arithmetic — so a hand's category and full
    tiebreak (ignoring flush, which depends on suit, not value) is a
    pure function of that one integer, looked up once instead of
    recomputed via counting/sorting on every call. `_build_value_
    lookup_table` precomputes this table ONCE, at import time, over all
    `C(13+5-1, 5) = 6,188` distinct 5-value multisets (including some no
    real 4-suit deck could ever deal, e.g. 5-of-a-kind — harmless, never
    looked up), sorted by prime product for `np.searchsorted`-based
    vectorized lookup (O(log 6,188) per hand, fully array-vectorized,
    not a Python loop). `_rank_five_batch` computes each hand's prime
    product (one vectorized elementwise multiply), looks it up, then
    applies flush as a pure category relabel afterward.
  - **A real mathematical guarantee, verified before relying on it, not
    assumed:** a flush's value pattern is ALWAYS exactly 5 distinct
    values — a real deck has only one card per (value, suit) pair, so 5
    same-suit cards can never repeat a value. This means the value-only
    lookup table (built ignoring suit entirely) can only ever return
    `STRAIGHT` or `HIGH_CARD` for a flush hand — and `rank_five`'s own
    `FLUSH`/`HIGH_CARD` tiebreak conventions are identical (both
    `sorted(values, reverse=True)`), confirmed by reading the existing
    scalar code, not assumed by analogy. So flush upgrade
    (`STRAIGHT`->`STRAIGHT_FLUSH`, `HIGH_CARD`->`FLUSH`) never needs to
    touch the tiebreak at all, just relabel the category — no other
    category can ever coincide with `is_flush=True`, so no other case
    needs handling.
  - **The strongest correctness signal available, not a sample:**
    `tests/test_hand_eval.py` gained `test_rank_five_batch_exhaustive_
    over_every_value_multiset` — EVERY one of the 6,188 distinct 5-value
    multisets a real hand can have (minus the physically-impossible
    ones), cross-validated against the trusted scalar `rank_five`
    reference, both without and (for the subset where it's physically
    possible — exactly 5 distinct values) with a forced flush. Mirrors
    this project's own "exhaustive enumeration where feasible" precedent
    (M19's flop/turn canonicalization tests) rather than trusting a
    random sample alone for a rewrite this central. Runs in well under a
    second (6,188 scalar comparisons, not millions), so it's cheap
    enough to run on every full-suite invocation, not a special one-off
    validation script thrown away after use.
  - **Measured, not assumed — the real payoff:** the identical
    `solve_flop_to_river` benchmark M46/M47 both used (3 combos/side,
    the production cap, at that iteration count): ~41.3s (M47's own
    post-fix baseline) -> **~6.5-8s** — a real **~5-6x** speedup, not
    the modest ~4% M47's own deck-precompute fix delivered. The full
    backend test suite's own wall-clock time is a second, independent
    confirmation: 401.60s, down from M47's 497.51s (both fresh runs, not
    cherry-picked) — real evidence this isn't a benchmark-specific
    artifact, since most of the suite doesn't touch `solve_flop_to_
    river` at all but does exercise hand evaluation broadly (equity.py,
    board_equity.py, cfr.py's multiway paths).
  - **Tests:** `python -m pytest tests/ -v` — 704 passed, zero
    regressions (up from M47's 703 — 1 new exhaustive test). No frontend
    changes.
  - **Scope:** engine only. `rank_five`/`best_hand_rank` (the scalar
    reference) are completely unchanged — still the permanent trusted
    ground truth every evaluator is validated against, per this
    project's own established discipline. The OTHER lever M47 named (a
    restructured two-phase solve, avoiding ~44x49 branches' worth of
    chance-tree construction when a caller only reads out one path)
    remains a real, separate, unattempted future direction — this
    milestone's own ~5-6x win came from a different part of the cost
    entirely (hand evaluation, not chance-tree construction), so the two
    levers are independent and could in principle compound if the
    second is ever pursued.

- **M49 — spend M48's speedup on range quality, not just latency:
  re-tune `/solve_river_from_path`'s own combo cap.** Every path-derived
  endpoint's cap constants were calibrated against the OLD, much slower
  hand evaluator — the most valuable, highest-leverage use of M48's
  ~5-6x win isn't a faster response at the SAME range width, it's a
  WIDER, less-approximated derived range at roughly the SAME wall-clock
  budget this project has consistently accepted as "tolerable." Chose
  the river endpoint first (not a blanket re-tune of every endpoint) —
  it's the most recently built, most expensive, and benefits the most
  from M48's win; the other endpoints' own caps are a natural, smaller
  follow-up, not attempted here (ship one coherent improvement per PR).
  - **Re-measured, not assumed to have simply scaled down 5-6x
    uniformly:** combo-pool-size scaling is no longer close to linear
    post-M48 — 3 combos/side (6 total) measured anywhere from ~11s to
    ~39s across repeated runs (a real, wide, honestly-reported spread,
    not a single cherry-picked number); 6/side (12 total) similarly
    ~18-40s; 9/side (18 total) jumped to ~76-110s, a genuine super-
    linear cliff distinct from ordinary run-to-run noise. Caught by
    re-running the same config twice before trusting a number this
    milestone would base a production constant on — the first K-sweep's
    own cap=6 reading (~17.56s) looked unusually fast in isolation, and
    a dedicated, twice-repeated re-measurement at cap=6 alone (~38.80s,
    ~39.41s) confirmed the TRUE cost is closer to ~40s, not ~18s — the
    honest number made it into the shipped comment, not the optimistic
    one.
  - **`RIVER_PATH_QUERY_MAX_COMBOS_PER_SIDE`: 3 -> 6** — DOUBLE the
    combo diversity per side, landing at roughly the SAME wall-clock
    cost (~40s) M46's own original cap=3 used to cost BEFORE M48's
    speedup. `DEFAULT_RIVER_PATH_QUERY_ITERATIONS`/`MAX_RIVER_PATH_
    QUERY_ITERATIONS` left unchanged (still `==default`, zero headroom)
    — iteration-count scaling at the new cap is still real and
    meaningful (20 iters ~39-40s, 50 iters ~54s, 100 iters ~75-76s,
    twice independently confirmed), so there was no room found to relax
    that side of the budget too, only the combo-cap side.
  - **Deliberately not pushed to cap=9** despite the real per-combo
    speedup, given the measured super-linear cliff there (~76-110s) —
    a real, named boundary this milestone chose not to cross without
    its own dedicated re-measurement pass, not an oversight.
  - **Tests:** no new tests — the existing 14 `/solve_river_from_path`
    tests already run against a fixture-patched cap
    (`FAST_RIVER_PATH_QUERY_MAX_COMBOS_PER_SIDE=1`, independent of the
    production constant), so they exercise HTTP plumbing correctness
    regardless of the production cap's own value — nothing about this
    change needed new coverage, only re-measurement.
  - **Verification:** `python -m pytest tests/ -v` — 704 passed, zero
    regressions (same count as M48 — no test additions, a constants-
    and-docs-only change). No frontend changes.
  - **What's still open:** re-tuning the OTHER path-derived endpoints'
    own caps (`MAX_PATH_QUERY_CLASSES_PER_SIDE`, `MAX_TURN_PATH_QUERY_
    CLASSES_PER_SIDE`, `MAX_MULTIWAY_PATH_QUERY_CLASSES_PER_POSITION`,
    `MAX_MULTIWAY_TURN_PATH_QUERY_CLASSES_PER_POSITION`) against M48's
    same speedup — each would need its own dedicated re-measurement,
    the same discipline this milestone applied to the river endpoint,
    not a blanket multiply-by-some-factor guess; and the OTHER M47-named
    speed lever (a restructured two-phase solve) remains unattempted.

- **M50 — extract the five path-derived endpoints' shared front half
  (`_derive_path_situation`).** Groundwork for a unified `POST /advise`
  (M51), split into its own PR per this project's "one coherent
  improvement per PR" rule — a pure, behavior-preserving refactor,
  proven by the existing 62 path-endpoint tests passing **completely
  unchanged**, before any new feature layers on top of it.
  - **The problem, measured not asserted:** five orchestrators
    (`_query_flop_from_path`, `_query_flop_multiway_from_path`,
    `_query_turn_from_path`, `_query_turn_multiway_from_path`,
    `_query_river_from_path`) each hand-rolled the SAME pipeline —
    cached preflop solve -> resolve action kinds -> `derive_ranges_from_
    path` -> terminal/live-count validation -> cap ranges -> expand to
    board-legal combos -> `postflop_action_order` -> derive the shared
    effective stack. Only the SOLVE stage and response shape genuinely
    differ. Concrete before/after: `derive_ranges_from_path` had **5
    call sites, now 1**.
  - **Honest about what this did and didn't buy:** `api/main.py` is
    roughly line-NEUTRAL (+218/-201) — the shared function carries a
    substantial design docstring explaining what's parameterized and
    why. The win isn't line count, it's that ~175 lines of five-times-
    duplicated pipeline became ~85 lines existing once. Stated plainly
    rather than dressed up as a size reduction it isn't.
  - **The real payoff, and the reason this was worth doing before
    M51:** `path_scenario.trained` — the confidence signal flagged as a
    "known, deliberate gap" in M29, M42, AND M44, deferred every time —
    kept needing a five-place change. It now has exactly ONE place that
    would need to change. (Still deferred here: it needs its own
    response-shape decision, which is M51's business, not a refactor's.)
  - **Deliberately parameterized, NOT unified away — these are real
    per-endpoint differences, not incidental drift:** the live-position
    rule (exactly 2 for the exact solvers vs. 3+ for MCCFR, each
    rejecting the other's case by name); class-level vs. combo-level
    capping (M46's river endpoint needs the finer lever, for its own
    measured reason); `path_field_name` (the flop endpoints call their
    field `action_path`, the deeper ones `preflop_action_path`, and
    error text should name whichever the client actually sent); and all
    five separately-measured cap constants, kept as-is. This is the
    exact failure mode this project has hit before (M32's `postflop_
    action_order` misapplication, M47's rejected lazy-chance idea) —
    unifying things that only LOOK the same.
  - **A real, small behavior improvement that fell out of the
    extraction, verified empirically:** a 3-live-position path sent to a
    2-position endpoint previously reached `postflop_action_order`'s own
    2-tuple unpack and surfaced as a bare `"too many values to unpack"`
    ValueError — still a 422, but useless to a caller. The two flop
    endpoints had no explicit check at all; only the deeper three did.
    Now every endpoint gives the same real explanation naming the
    sibling endpoint that DOES serve that case: `"action_path leaves 3
    live positions, not 2 — use /solve_flop_multiway_from_path for a
    3+-survivor situation"`. Confirmed live before pinning it in a test.
  - **`_PathSituation.capped_scenario` is `None` for combo-level
    capping, populated for class-level** — not an inconsistency: the
    canonical-library path (`query_strategy_from_path`) has a documented
    class-dicts-only contract (M20's crux design finding), so it needs a
    real `StartingHand`-keyed `PathScenario`; a combo-capped range has
    no meaningful class-level equivalent to hand it.
  - **Tests:** 8 new — 7 direct unit tests of `_derive_path_situation`
    itself (both capping modes, the exactly-one-mode `RuntimeError`
    guard, non-terminal rejection naming the client's own field, both
    live-count rejections naming the sibling, a real 3-live acceptance
    including the N-general equal-stacks guarantee), plus the existing
    3-live-survivor endpoint test strengthened from a bare 422 assertion
    to pinning the improved message. The board-blocks-every-combo guard
    test uses a real, empirically-verified constructible case (at a
    1-class cap, BB's top class on this path is the pair `22`, and a
    board of three deuces blocks all six of its combos — a pair being
    the only class shape a 3-card flop can fully block) rather than a
    contrived one; an initial placeholder version of this test that
    asserted something trivially true was caught and replaced before
    shipping.
  - **Verification:** `python -m pytest tests/ -v` — 712 passed, zero
    regressions (up from M49's 704 — 8 new tests). The 62 pre-existing
    path-endpoint tests needed NO modification, which is the actual
    proof the refactor preserved behavior. All five orchestrator
    signatures verified unchanged, so `_prewarm_common_depths`' own
    positional call sites (a path the test suite never exercises, since
    pre-warm is disabled there) still bind correctly. No frontend
    changes.
  - **Next (M51, the reason this exists):** `POST /advise` — one
    endpoint taking a full situation (street depth inferred from which
    fields are present), dispatching into this core, adding `hero_cards`
    (force-included BEFORE capping, so a hand outside the top-K isn't
    silently absent from its own advice) and a `source` field naming
    which backend answered (`exact`/`mccfr`/`library_hit`/`library_
    miss`) so the canonical library's real ~7,000x hit speedup is kept
    but its inability to report `trained` is visible rather than hidden.
    The missing multiway-river cell becomes a dispatch case, not a 6th
    duplicate.

- **M51 — `POST /advise`: the unified front door the whole v3 vision
  needed.** One request describing a real situation (your cards, the
  board, table size, and what everyone did across every street), one
  response with advice for the decision actually faced. Built on M50's
  extracted core; each (street, table size) cell delegates to whichever
  sibling orchestrator already serves it, so this is a front door, not
  a second implementation to keep in sync.
  - **Street depth is INFERRED, not client-declared** — from which
    fields are present, mirroring how a hand actually unfolds. A
    `street` field the client sets independently of its own board/card
    fields would be a second source of truth that can disagree with
    them. `_infer_street` rejects every partial or skipped combination
    (a river card with no turn card, a turn card with no flop action,
    an action path with no board, and so on) — 7 such combinations are
    parametrized in tests.
  - **`hero_cards` — the feature the product actually needed, plus the
    trap underneath it.** Every prior endpoint returns a strategy for
    EVERY combo in the range, leaving the caller to find their own hand.
    The naive fix (accept hero's cards, look them up) has a real trap
    found while scoping, not after shipping: the derived range is capped
    to the top-K combos, so a hand outside K would be silently ABSENT
    from the very solve meant to advise it — exactly the marginal case
    advice matters most for. Fixed by force-including hero's combo in
    every live position's range BEFORE capping, with `hero.in_range`
    reporting honestly whether it survived the cap on its own weight.
    Verified live: `AsKs` on a real 3-bet-call line came back
    `in_range: false` WITH real advice (check 27% / bet 43% / jam 30%)
    — with no force-inclusion it would have returned nothing at all.
    The real cost is stated, not hidden: at most one extra combo per
    live position, a genuine if small solve-cost increase.
  - **Force-inclusion had to reach TWO places, not one** — a real
    subtlety caught by tracing the library path rather than assuming
    symmetry: the heads-up-flop cell solves from `capped_scenario`'s
    CLASS-level ranges, not from `position_ranges`, so combo-level
    force-inclusion alone would have silently had no effect on exactly
    that one endpoint. Hero's CLASS is force-included there too — class
    level, not combo level, because `library.query_strategy_from_path`'s
    contract is class-dicts-only (M20's crux finding: a suit-asymmetric
    combo dict breaks canonical reuse).
  - **`source` makes the family's one real asymmetry visible instead of
    hidden** (`"exact"` / `"mccfr"` / `"library_hit"` / `"library_miss"`
    / `"preflop"`). Per the user's own chosen option: keep the canonical
    library for the heads-up-flop cell — its hit is ~0.2ms against a
    ~20s miss, and trading that for a tidier uniform table would be a
    bad deal — but surface `trained: null` as an EXPLICIT null rather
    than a silently-omitted field, so a caller can tell "no confidence
    data available here" from "every hand is trained". Verified live:
    first call `library_miss`, repeat `library_hit`.
  - **A whole new street, nearly free: PREFLOP advice** — no
    path-derived endpoint ever served it, yet it's the most common
    decision in poker and the advisor is useless without it. Reads
    straight off the cached preflop solve. Note the deliberately
    INVERTED terminal requirement: every postflop cell needs the
    preflop action to have CLOSED before a board is dealt, whereas
    preflop advice needs it still OPEN with someone left to act.
  - **A real bug the smoke test caught before any test was written:**
    preflop strategies are keyed by hand CLASS (`"AKs"`), every postflop
    street by concrete combo (`"AsKs"`) — the 169-class abstraction is
    v1's own foundational choice. A route assuming one key shape
    silently returned `strategy: null` for hero preflop. Fixed with a
    `hero_key` the answering cell supplies, since only it knows which
    shape its own strategy dict uses; pinned by its own test.
  - **A second real bug, caught by this milestone's own test rather
    than review:** the unsupported ("river", multiway) cell raised a
    bare `KeyError` from the iteration-cap lookup BEFORE reaching its
    carefully-worded explanation, so callers got `"unsupported
    street/table-size combination: ('river', True)"`. Fixed by checking
    `_ADVISE_UNSUPPORTED_CELLS` before the cap lookup, so the real
    reason wins.
  - **One cell deliberately unfilled, with the real reason recorded:**
    ("river", multiway). `solve_flop_to_river_multiway` (M39) exists,
    but its MCCFR `chance_data` holds only SAMPLED `(terminal, card)`
    pairs, so a legal-but-unsampled river card needs the on-demand
    branch build `ensure_flop_turn_multiway_branch` (M44) provides one
    hop shallower — and whether a SECOND chained hop needs that
    identical treatment or a structurally different one is the real
    open design question M44's own entry already named. Scoping this
    milestone corrected an earlier optimistic claim that the missing
    cell would "fall out as a dispatch case": it falls out
    structurally, but that design question is real work, not free.
  - **Tests:** 20 new — all six served cells (preflop, flop HU/MW, turn
    HU/MW, river HU) asserting the right `source` and `trained` shape;
    hero force-inclusion returning real advice at `in_range: false`;
    hero keyed by class preflop; hero blocked by the board; malformed
    hero cards; the unfilled river-multiway cell naming its reason; 7
    parametrized partial/skipped street combinations; the per-cell
    `solve_iterations` cap; and an over-long action path.
  - **Verification:** `python -m pytest tests/ -v` — 732 passed, zero
    regressions (up from M50's 712 — 20 new). No frontend changes; per
    the user's own stated priority the frontend is a secondary tool, so
    an `/advise`-backed UI is a natural follow-up, not part of closing
    this capability.
  - **What this closes, and what it doesn't:** the engine now answers
    the exact question the v3 vision opens with — hole cards, board,
    position, action history in; GTO advice out — through ONE endpoint,
    at every street, heads-up or multiway (bar the one named cell).
    Still open: the river-multiway cell above; re-tuning the remaining
    caps against M48's speedup (M49 did the river's only); the
    two-phase-solve speed lever (M47); surfacing `path_scenario.trained`
    (now a one-place change thanks to M50, still needing its own
    response-shape decision); and live-table integration, which the
    user explicitly deferred.

- **M52 — surface the derived-range confidence signal, and fix a real
  `/advise` dispatch bug found while proving it fires.** Closes the gap
  M29 measured and M29/M42/M44 each deferred exposing — now a one-place
  change, exactly as M50's extraction promised.
  - **Why this one mattered most of what was left:** M29 measured a real
    6-max line whose derived range came back *exactly uniform* —
    "confident-looking, fabricated, and silently indistinguishable from
    a genuinely converged one." For a tool whose entire job is giving
    advice someone might act on, silently-fabricated input is worse than
    a slow answer or a missing feature.
  - **The response-shape decision M29/M42/M44 all deferred, now made:**
    a per-position SUMMARY (`range_confidence: {position: {trained_
    classes, total_classes, fully_trained}}`), not a per-hand map. A
    full per-hand map for every live position is mostly noise for a
    caller asking "can I trust this"; counts plus a boolean answer that
    directly, and `hero.range_trained` covers the one hand a caller
    actually holds. Computed over the classes that actually SURVIVED
    capping, not the full derived range — advice is only ever built from
    what got solved, so confidence over discarded classes would dilute
    the number that matters.
  - **Two confidence fields that are easy to conflate, deliberately kept
    separate and documented as such:** `hero.trained` is about the
    POSTFLOP solve node the advice was read from; `hero.range_trained`
    is about the PREFLOP derivation that produced the range fed into
    that solve. Either can be untrustworthy independently of the other.
  - **Proven to actually fire, not just to exist:** a signal that's
    always `True` proves nothing, so this was verified against M29's own
    measured case — a deep 6-max 3-bet line — which reproduces exactly:
    `UTG: 0/3 trained, fully_trained: False`, `hero.range_trained:
    False`, while the other survivor is cleanly 3/3. Pinned by a test
    that asserts at least one position is untrained on that line, plus
    a companion test that a shallow heads-up line comes back cleanly
    fully trained (so the signal isn't just always-False either).
  - **The real bug this surfaced, and it was a serious one:** proving
    the signal fires needed a 6-max line, which immediately 422'd —
    `/advise` picked its solver from `request.players` (the ORIGIN table
    size) rather than from how many positions actually SURVIVE to the
    flop. A 6-max hand where everyone folds and two players see the flop
    heads-up — *the most common real full-ring shape*, and precisely
    what M29 was built to support — was routed to the multiway cell,
    which then correctly refused it. `/advise` was unusable for exactly
    the case M29 existed to serve. Fixed with `_live_position_count`
    (counts from the resolved node's own `folded` set — a tree walk over
    an already-cached solve, not a second solve, and deliberately not
    `derive_ranges_from_path`, whose reach-multiplication this question
    doesn't need). Threaded through the cap lookup and unsupported-cell
    check too, since both were keyed on the same wrong question.
    Regression-pinned by its own test.
  - **Scope, stated honestly:** `range_confidence` is surfaced on
    `/advise` only, not on the five sibling endpoints — the fix itself
    is in the one shared place (`_derive_path_situation`, per M50), but
    surfacing it on each sibling would mean five more response-model
    changes. `/advise` is the front door a real consumer uses; the
    siblings remain the older, narrower-purpose endpoints. A caller
    needing the signal there can switch to `/advise`.
  - **Verification:** `python -m pytest tests/ -v` — 736 passed, zero
    regressions (up from M51's 732 — 4 new). No frontend changes.
  - **What's still open:** the (river, multiway) cell; re-tuning the
    remaining caps against M48's speedup; the two-phase-solve lever
    (M47); and live-table integration, which the user explicitly
    deferred.

- **M53 — fill the last (street × table size) cell, by generalizing
  rather than duplicating.** Answers the design question M44 named and
  left open, and completes the matrix: every street (preflop → river)
  now works at every supported table size through `/advise`.
  - **M44's open question, answered by reading the code rather than
    assuming:** does a SECOND chained chance-hop (turn → river) need
    structurally different on-demand-build treatment than the first?
    **No.** `chance.build_mccfr_chance_branch` was already fully
    hop-agnostic — it derives the next board from whatever `board` it's
    handed, and self-guards `chain_to_river and len(next_board) < 5`, so
    handing it a 4-card (flop+turn) board produces a river branch whose
    own `chance_fn` is correctly `None` with no special-casing at all.
  - **So the choice at this decision point was generalize vs. duplicate,
    and generalize won on every axis** (the "widest, most future-proof
    base" the user asked for): `ensure_flop_turn_multiway_branch` →
    `ensure_mccfr_chance_branch`, a rename plus a one-line
    `chain_to_river` passthrough, serving BOTH hops — instead of a
    second near-copy. M44's original name is kept as an alias so nothing
    that imported it breaks; the flop_turn-specific name simply stopped
    being accurate once the same function was proven to serve the river.
  - **Same call made one level up:** rather than write a third multiway
    orchestrator, `_query_turn_multiway_from_path` was generalized to
    walk EITHER depth (`turn_action_kinds`/`river_card` optional; their
    presence selects `solve_flop_to_river_multiway` over `solve_flop_
    turn_multiway`). The river hop's code is visibly the turn hop's,
    one card-richer board and one street-deeper remaining stack — which
    is itself the evidence for the "same treatment" finding above.
    Follows M50's precedent: the existing endpoint's own 10 tests pass
    unchanged, which is the proof the generalization preserved it.
  - **`to_river` is part of the solve cache key** — the two solvers
    produce genuinely different results (`chain_to_river` populates a
    second level of `chance_fn`), so a turn-depth result must never be
    served to a river-depth query. Same collision reasoning every other
    cache key in this file already applies to `players`.
  - **A real subtlety carried over, not rediscovered:** the remaining
    stack entering the river subtracts the TURN street's own fresh,
    0-based `invested` (per `game_tree.StreetConfig`'s per-street reset),
    not a cumulative figure — the same trap `_query_river_from_path`
    already documents for the 2-position path.
  - **Tests:** 3 new — the alias identity; a direct two-hop engine test
    proving `ensure_mccfr_chance_branch` builds a real 5-card-board
    river branch from a 4-card board with `chance_fn is None` (the
    "second hop is the LAST hop, not an infinite chain" guard); and a
    live `/advise` river-multiway test. The previous test asserting the
    cell was UNSUPPORTED was flipped rather than deleted, so regressing
    back to unsupported would still fail — joined by a live assertion
    that `_ADVISE_UNSUPPORTED_CELLS` is empty, so re-adding a gap is a
    deliberate, visible act rather than something that quietly reappears.
  - **Verification:** `python -m pytest tests/ -v` — 739 passed, zero
    regressions (up from M52's 736 — 3 new). Verified live end to end: a
    real 3-way hand walked preflop → flop → turn → river, returning SB's
    river decision with hero's own advice attached. No frontend changes.
  - **What's still open:** re-tuning the remaining caps against M48's
    speedup (M49 did the river's only); the two-phase-solve lever (M47);
    and live-table integration, which the user explicitly deferred.

- **M54 — re-tune the four remaining path-derived caps against M48's
  speedup, and find the one cell it barely helped.** M49 re-tuned only
  the river's; its own note insisted each other cap needed its own
  dedicated measurement rather than a blanket multiply. Done here.
  - **A real measurement flaw caught and corrected before trusting any
    number** — the same discipline M49 applied to itself: the first
    sweep cleared the preflop cache in only ONE of its four loops, so
    preflop-solve cost leaked into whichever measurement ran first. It
    gave itself away by producing an impossible reading — flop-multiway
    at cap=2 measuring 36.59s but cap=4 measuring 14.09s, a *larger* cap
    running *faster*. Re-run with BOTH preflop legs pre-warmed, so every
    number reflects only what the cap actually controls.
  - **The corrected numbers** (preflop excluded throughout): flop
    heads-up 6->3.47s, 10->11.17s, 14->22.56s. Turn heads-up 2->32.77s,
    3->78.84s, 4->107.27s. Flop multiway 2->7.42s, 4->16.80s,
    6->17.03s. Turn multiway 2->1.38s, 4->2.32s, 6->2.21s.
  - **What changed:** `MAX_PATH_QUERY_CLASSES_PER_SIDE` 6 -> 10 (~67%
    more range fidelity at ~11s, with cap=14 left as measured headroom);
    `MAX_MULTIWAY_PATH_QUERY_CLASSES_PER_POSITION` and `MAX_MULTIWAY_
    TURN_PATH_QUERY_CLASSES_PER_POSITION` both 2 -> 6.
  - **A real structural ceiling found, not assumed:** both multiway caps
    measured cap=6 as costing essentially the SAME as cap=4 — because at
    `players != 2` the preflop leg solves over `DEMO_MULTIWAY_HANDS`'
    own 8 classes (per `_get_or_solve_preflop_raw`), and this path's
    derived range has fewer than 6 with meaningful weight, so the extra
    headroom simply doesn't bind. Raised to 6 anyway, precisely because
    it's measured-free on this path while giving real extra fidelity on
    a path whose derived range IS wider — with the honest caveat, in the
    comment, that such a path would cost more than the number measured.
  - **The one negative finding, kept rather than glossed:**
    `MAX_TURN_PATH_QUERY_CLASSES_PER_SIDE` stays at **2**. M48 helped
    this cell only ~1.4x (M26's ~45.9s -> 32.77s), far less than the
    3-8x every sibling saw, so the next class step (78.84s) still costs
    more than the bracket M26 deliberately chose cap=2 to stay inside.
    The speedup here is spent on LATENCY, not range width. Why it
    benefited least is a useful pointer recorded in the constant's own
    comment: M47's profile found `solve_flop_turn` dominated by
    `cfr._solve_recurse`'s tree traversal (531K recursive calls), not by
    hand evaluation — so M48 could only ever move part of it. That makes
    this the cell the still-untried two-phase-solve lever would most
    benefit, which is the next item on the list.
  - **Verification:** `python -m pytest tests/ -v` — 739 passed, zero
    regressions (constants-and-docs-only; the path-endpoint tests run
    against fixture-patched caps independent of the production values).
    No frontend changes.

- **M55 — the speed lever that was actually there: memoize chance-node
  equity tables. Scoped as the two-phase solve; became something better
  and provably correct.** Item 3 on the user's own ordered list.
  - **Scoping started by re-checking M54's own claim, and found it
    WRONG.** M54's comment asserted `solve_flop_turn` was "dominated by
    `cfr._solve_recurse`'s tree traversal, not by hand evaluation" —
    derived from a stale pre-M48 profile AND by misreading cProfile's
    CUMULATIVE time as self time. Re-profiled by SELF time on current
    code: `build_board_equity_table` is 9.65s self / 30.52s cumulative
    of 41.16s total (**~74%**), while `_solve_recurse`'s own self time is
    just 5.05s (~12%). Equity-table CONSTRUCTION dominates, not
    traversal. The corrected comment ships in `MAX_TURN_PATH_QUERY_
    CLASSES_PER_SIDE`'s own docstring rather than being quietly fixed.
  - **That correction is what found the real lever.** A chance branch's
    equity table is a pure function of `(next_board, combos)` — it does
    NOT depend on which `terminal` the chance node hangs off, since the
    terminal only influences the branch's TREE (via `remaining_stack`).
    But `build_chance_node` is called once per showdown-eligible flop
    terminal, and each independently rebuilt all ~46-49 identical
    tables. **Measured on a real `/solve_turn_from_path` query: 343
    builds, 49 distinct inputs — exactly 7.00x redundancy**, against the
    ~74% bottleneck above.
  - **Chosen over the two-phase solve, and this was the decision point
    the user's "widest, most future-proof base" instruction governed.**
    The two-phase solve (solve flop+turn cheaply, then re-solve the one
    requested turn subtree) is speculative: it changes the answer, needs
    its own accuracy measurement, and risks repeating M17/M18's card
    abstraction — machinery built, measured, found not to be the lever.
    Memoization is correct BY CONSTRUCTION (same pure function, same
    arguments), targets the same dominant cost, and helps every
    chance-dispatching solver rather than one cell. Proven, not argued:
    a same-inputs comparison came back **bit-identical** (max strategy
    difference exactly 0.0) while running faster.
  - **Per-solve cache, not a module global** — scoped so nothing leaks
    across requests, pools, or threads, the same caller-supplied-dict
    pattern `chance_data` itself already uses. Forwarded into the
    chained river hop too, or the second level would silently lose the
    benefit.
  - **Measured payoff, and it landed hardest exactly where M54 said
    there was no headroom:** turn heads-up cap=2 32.77s -> **10.18s**
    (3.2x), cap=3 78.84s -> **19.93s** (4.0x), cap=4 107.27s ->
    **25.04s** (4.3x). River heads-up cap=6 ~40s -> **17.18s**, cap=9 ->
    31.72s. The full backend suite's own wall clock fell 457s -> 415s as
    independent corroboration.
  - **So two caps rose, and M54's own conclusion is now obsolete:**
    `MAX_TURN_PATH_QUERY_CLASSES_PER_SIDE` 2 -> **4** (cap=4 now costs
    LESS than cap=2 did before — double the fidelity AND faster), and
    `RIVER_PATH_QUERY_MAX_COMBOS_PER_SIDE` 6 -> **9** (50% more combo
    fidelity, still faster than the previous setting).
  - **Tests:** 3 new in `tests/test_chance.py` — that without a cache
    each terminal rebuilds its own tables; that with a shared cache a
    second, genuinely DIFFERENT terminal builds zero new tables while
    still getting a different tree (proving the cache shares only what's
    terminal-independent, and shares the same array objects rather than
    merely equal ones); and that tables are identical with and without
    the cache.
  - **Verification:** `python -m pytest tests/ -v` — 742 passed, zero
    regressions (up from M54's 739 — 3 new). No frontend changes.
  - **The two-phase solve remains untried**, now with a much higher bar
    to clear: it would have to beat a lever that is already 3-4x and
    lossless. Whether the remaining `_solve_recurse` self time (~12%) is
    worth an architectural change is a genuinely open question, and a
    smaller prize than it looked before this profile.

- **M56 — a frontend for `/advise`: the app finally has a front door.**
  `AdviseSolver.tsx` + an "Advisor" tab, placed FIRST in the tab bar —
  the seven existing pages are narrower, single-purpose tools; this is
  the one that answers the question the v3 vision actually opens with.
  - **Deliberately ONE component, not a 4th/5th sibling of FlopSolver/
    TurnPathSolver.** The whole point of `/advise` (M50-M53) is that
    street depth and table size are one request shape; splitting the UI
    back apart per street would reintroduce on the client exactly the
    sprawl M50/M51 consolidated away on the server. A "how far did the
    hand go" street toggle maps 1:1 onto the endpoint's own street
    inference — the UI simply omits fields for streets that haven't
    happened.
  - **Surfaces three things no other page in this app does:** hero's OWN
    hand's advice (the actual product question — every other page makes
    you find your hand in a list); `source`, naming which backend
    answered; and `range_confidence` (M52), flagging when part of the
    solved-against range was the untrained default. Hero's card gets its
    own visual weight (`.hero-advice`) rather than being one more row.
  - **Both of hero's confidence signals are shown, and distinguished** —
    `in_range: false` ("your hand was added to the range so it could be
    solved for, treat it as thinner") and `range_trained: false` ("the
    preflop derivation for its class wasn't fully backed by real
    solving"). Easy to conflate; they mean different things.
  - **Postflop action lines are deliberately generic** ("Checked
    through", "Bet, everyone called") rather than `TurnPathSolver`'s own
    hand-enumerated `FLOP_PRESETS`, which are calibrated against the
    2-position tree and would silently drift at 3+. Both generic lines
    stay structurally legal at ANY live-position count, which is what
    lets one component serve every table size. The real fix remains a
    "what's legal on this street from here" walker — an open gap since
    M26, unchanged here.
  - **A real bug caught ONLY by live browser verification, for the third
    time in this project's history:** `/advise` 404'd in dev because
    `frontend/vite.config.ts`'s proxy prefix-matches, and `/advise` is a
    new prefix not covered by `/solve`. The unit tests stub `fetch`, so
    they structurally cannot catch a proxy gap. That's now M10's
    `/equity`, M25's `/preflop_walk`, and this — three separate
    milestones where a route not named `/solve_something` silently fell
    through to the SPA's index.html. The pattern is recorded in the
    config's own comment; anything adding a new top-level route should
    check it.
  - **Verification:** `npm test` — 164 passed (up from M45's 154 — 10
    new). `tsc --noEmit` and `npm run lint` both clean. No backend
    changes. Live-verified end to end: the Advisor tab walks a real
    preflop tree, and asking with `hero_cards=7c2d` returns "fold 99%,
    call 1%" — a sane answer to the exact question the v3 vision opens
    with — confirmed `POST /advise -> 200 OK` via network inspection
    (with the earlier 404 still visible in the log as proof the proxy
    fix was the thing that mattered).

- **M57 — whole-project audit: code quality, redundancy, and speed.**
  `docs/project-audit-2026-08-21.md` (new), mirroring `full-table-
  diagnostic-2026-08.md`'s own role before M27-M34: a measured
  checkpoint to inform prioritization, with nothing fixed as part of it.
  - **Headline: the codebase is structurally healthy.** Zero dead public
    functions, 906 passing tests (742 backend + 164 frontend), clean
    lint/type pass, engine/API boundary enforced by test. The findings
    are about accumulated SURFACE AREA, not defects.
  - **The one verified, immediately actionable finding:** `GET /solve/
    {stack_bb}` caches a formatted response in `_cache` while every
    path-derived endpoint caches a raw `StrategyResult` in `_preflop_
    raw_cache` — so at `players == 2` the identical preflop spot is
    solved TWICE (~3.2s wasted) on the most likely first user journey.
    Confirmed by inspecting both caches after real requests, not
    inferred. Already solved for `players != 2` (M29 shares one cache
    there); heads-up just never got the same treatment.
  - **Other redundancy found:** ~6 endpoints `/advise` now supersedes
    (each with its own near-duplicate schema); the same combo-row markup
    hand-rolled **11 times across 8 frontend components**; 8 tabs of
    which 5 are slices of what the Advisor now does; `api/main.py` at
    3,441 lines (2.3x the next largest file); 28 cache/lock globals with
    no registry, which the test fixture must clear by hand in two places
    (a recurring per-milestone footgun).
  - **Benchmarks, 24 endpoints at production settings** — full table in
    the document. Two findings worth carrying forward: **3-max preflop
    (23.92s) is slower than 6-max (11.33s)** because it runs 100,000
    iterations against their 300, meaning 6/9-max get ~333x less solving
    and there IS cost headroom (M27's convergence instability, not cost,
    is the blocker — and it predates M48/M55's speedups); and **multiway
    postflop is FASTER than heads-up** (turn 2.22s vs 10.78s) purely
    because its preflop leg solves over 8 classes rather than 169 — a
    fidelity gap wearing a speed win's clothing, worth remembering
    before treating multiway advice as equally trustworthy.
  - **Trajectory recorded:** the slowest real path is now river heads-up
    at 16.65s; turn heads-up advice was ~46s pre-M48 and is 10.78s now,
    at HIGHER range fidelity (M54/M55 raised the caps).
  - **Explicitly not recommended:** the two-phase solve (M47's other
    named lever). M55 found the real bottleneck, fixed it losslessly for
    3-4x, and left `_solve_recurse`'s own self time at ~12% — a
    speculative architectural change that alters solver output, for a
    ~12% slice, is a bad trade now.
  - **Prioritized recommendations (7), ranked by value/effort:** unify
    the preflop caches; extract a `<ComboRow>` component; add a cache
    registry; split `api/main.py`; revisit 6/9-max iteration budgets
    (measurement first — M27 warns more iterations made things worse);
    decide the fate of superseded endpoints/tabs; restructure CLAUDE.md.

- **M58 — audit recommendation #1: unify the two preflop caches, and
  fix a second bug the unification exposed.** First item of
  `docs/project-audit-2026-08-21.md`'s prioritized list, worked in
  order under the user's standing "auto-merge, take the deepest option
  at every fork" directive.
  - **The audit's own verified finding, fixed:** `GET /solve/{stack_bb}`
    kept its own formatted-response cache (`_cache`) while every
    path-derived endpoint cached a raw `StrategyResult` in `_preflop_
    raw_cache` — so at `players == 2` the identical preflop spot was
    solved TWICE. `_cache`/`_cache_lock`/`_get_or_solve` are deleted
    outright; there is now exactly ONE preflop cache. Already true for
    `players != 2` since M29; heads-up simply never got the same
    treatment.
  - **A SECOND, previously-unrecorded bug the unification exposed —
    and the reason the deep fix was the right call:** `GET /solve/
    {stack_bb}?position=BB` at heads-up **silently returned BTN's
    strategy**. The parameter was accepted and ignored, while the
    multiway branch honored it (`format_solve_response(result,
    position=position)` existed only there). Confirmed empirically
    before fixing. A minimal "make one helper call the other" fix would
    have left this in place; formatting at the ROUTE instead — one code
    path for every table size — fixes it for free. Silent-wrong-answer
    bugs are exactly what this project's own `trained`/`source` work
    exists to prevent, so leaving one in the oldest endpoint would have
    been incoherent.
  - **Measured payoff, larger than the audit predicted:** the audit
    estimated ~3.2s saved on one user journey. The full backend suite's
    own wall clock fell **415s -> 205s** — the duplicate solve was being
    paid repeatedly across the suite, not just once per session. A real,
    independent corroboration that the redundancy was genuine.
  - **Tests:** 4 new — one pinning the audit's own finding as a
    regression (hitting `/solve` then a path endpoint leaves exactly ONE
    cached solve), two pinning `position` at heads-up AND multiway, and
    one for an invalid position.
  - **Verification:** `python -m pytest tests/ -v` — 746 passed, zero
    regressions (up from M57's 742). No frontend changes.

- **M59 — audit recommendation #2: extract `<ComboRow>`.** Second item
  of `docs/project-audit-2026-08-21.md`'s list. Nine hand-rolled copies
  of the same strategy row, across seven components, become one.
  **164 lines deleted, 15 added.**
  - **The decision at the fork, and why the "deepest" answer was TWO
    components rather than one:** the audit counted 11 similar-looking
    rows, but reading them found **two structurally different shapes**,
    not one. A *strategy row* (full-width bar, gradient across the
    action mix, breakdown text, optional confidence indicator) answers
    "how is this hand's action split". A *percentage row*
    (`DetailPanel`/`EquityCalculator` — width tracks a value, single
    flat color) answers "how much of the whole is this". Merging them
    would produce a component whose bar means two different things
    depending on props — exactly the "unify things that only look
    alike" mistake this project has hit repeatedly (M32's `postflop_
    action_order` misapplication, M47's rejected lazy-chance idea,
    M50's own deliberately-parameterized differences). **Two honest
    components beat one dishonest one**, so only the 9-user shape was
    extracted and the reasoning is recorded in `ComboRow`'s own
    docstring so the next person doesn't "finish the job" wrongly.
  - **`label` is optional**, because `AdviseSolver`'s hero card names
    the hand in its own heading — repeating it in the row would be
    redundant. That variant would have been the easy thing to miss in a
    blind find-and-replace; it's covered by its own test.
  - **`trained` defaults to `true`** — an absent indicator means "trust
    this", which is the common case, so callers that have no confidence
    signal (e.g. the canonical-library-backed one, whose `trained` is
    structurally `null`) need pass nothing.
  - **Verification:** `npm test` — 169 passed (up from M56's 164 — 5
    new `ComboRow` tests). **Every pre-existing component test passed
    unmodified**, which is the actual proof the extraction preserved
    behavior. `tsc --noEmit` and `oxlint` clean. Live-verified in the
    browser: a real `/advise` query rendered 170 rows through the shared
    component, including the label-less hero variant. No backend
    changes.

- **M60 — audit recommendation #3: a cache registry.** Third item of
  `docs/project-audit-2026-08-21.md`'s list. 28 ad-hoc globals (14 dicts
  + 14 separately-declared locks) become 14 self-registering
  `_SolveCache` objects.
  - **The deepest option, and why it beat the minimal one:** the audit
    suggested "an `ALL_CACHES = [...]` list the fixture iterates". That
    would still be a hand-maintained inventory — the same class of thing
    that was already being forgotten. A class that **registers itself on
    construction** makes forgetting structurally impossible instead of
    merely discouraged, and simultaneously fixes the second half of the
    problem the audit noted: a dict and its lock were two independent
    globals kept paired by convention alone.
  - **`tests/test_api.py`'s fixture went from clearing 13 caches by hand
    in TWO places (setup and teardown) to one `_SolveCache.clear_all()`
    call each.** Every endpoint milestone in this project's history had
    to remember to patch both lists; that footgun is gone.
  - **Deliberately exposes `.entries` and `.lock` rather than forcing a
    `get`/`set` API** — the locking DISCIPLINES here genuinely differ and
    that difference is load-bearing: most helpers hold the lock only
    around the dict access (accepting a concurrent double-solve),
    `_query_flop`/`_query_flop_from_path` hold it across the whole
    `query_strategy` call (M22, because that primitive has no
    concurrency control of its own), and `_query_turn_multiway_from_path`
    also guards `ensure_mccfr_chance_branch`'s in-place mutation (M44).
    Collapsing those into one API would have quietly changed three
    endpoints' concurrency behavior — the same "unify things that only
    look alike" trap M50 and M59 each had to resist. **The class owns
    storage and registration; each call site keeps owning its policy.**
  - **A real distinction the audit missed, surfaced as an
    `AttributeError` during the work rather than as a review note:**
    `_flop_query_library` and `_path_query_libraries` are NOT endpoint
    response caches — they are the `library` dict `poker_solver.library.
    query_strategy` itself owns and mutates, whose documented contract
    is a plain dict. Every call site now hands the engine `.entries`,
    never the wrapper. Making `_SolveCache` masquerade as a dict would
    have "fixed" the error while cementing exactly the implicit coupling
    that made the distinction easy to miss.
  - **Tests:** 3 new — that the registry covers every module-level cache
    (verified by a live scan of the module rather than a hardcoded list,
    which would reintroduce the very inventory this removes), that
    `clear_all()` empties genuinely-populated caches, and that every
    registered cache bundles both a dict and a real lock.
  - **Verification:** `python -m pytest tests/ -v` — 749 passed, zero
    regressions (up from M59's 746). No frontend changes.

- **M61 — audit recommendation #4 (first half): split `api/main.py`.**
  3,441 lines -> **2,973**, with `api/config.py` (450) and
  `api/caches.py` (174) carved out. The layering is a clean one-way
  chain with no cycles: `config <- caches <- main`.
  - **Staged deliberately, not done in one shot.** The audit itself
    flagged this as "genuinely riskier than #1-#3 — it touches every
    endpoint at once", and the biggest slice (the ~1,800 lines of
    `_get_or_solve_*`/`_query_*` orchestrators) is left for its own
    milestone. This mirrors M50-before-M51 exactly: extract the
    foundation, prove it, then move the bulk onto it. Staging a risky
    refactor IS the more thorough answer, not a lesser one — a
    single-shot 3,400-line move with no intermediate checkpoint would
    have been faster to write and much harder to trust.
  - **A real constraint that shaped the design, thought through BEFORE
    moving anything:** `tests/test_api.py` monkeypatches ~10 constants
    as `api_main.<CONST>` to shrink demo pools for speed. Had `main.py`
    referenced them as `config.X`, or had the orchestrators moved out in
    this same step, those patches would have silently stopped taking
    effect — tests would still pass while testing the *unshrunk*
    production pools, i.e. slower and no longer testing what they claim.
    Avoided by importing every name INTO `main.py`'s own namespace and
    keeping the orchestrators there for now, so every existing read and
    every existing monkeypatch resolves exactly as before.
  - **The proof, and it is the same one M50 used:** `git diff tests/` is
    **empty**. 749 passed with zero test modifications. A refactor that
    needed its tests edited to pass would not have demonstrated anything
    about behavior preservation.
  - **Two small things caught by running rather than reading:** `logger`
    had been swept into the constants block by a line-range extraction
    (it belongs with the app that logs, not with tunable values), and
    `_SolveCache` itself wasn't re-exported because the extraction
    matched assignments and the class is a `class` statement. Both
    surfaced immediately as import/attribute errors.
  - **Every constant kept its full explanatory comment.** Those comments
    are why this project's cost decisions survive across milestones, so
    they moved with the values rather than being summarized away —
    `config.py` is 450 lines for 42 constants precisely because of that.
  - **Verification:** `python -m pytest tests/ -v` — 749 passed, zero
    regressions and zero test changes. No frontend changes.
  - **Remaining for the second half:** move the ~1,800 lines of
    orchestrators into `api/solving.py`, which will require switching
    the constant reads to `config.X` and updating the fixture's
    monkeypatch targets accordingly — a real, deliberate test change,
    unlike this half.

- **M62 — audit recommendation #4 (second half): extract
  `api/solving.py`.** `api/main.py` **3,441 -> 1,273 lines** across
  M61+M62 (a 63% reduction), now four modules in a one-way chain:

        config (450)  <-  caches (174)  <-  solving (1,776)  <-  main (1,273)

  `main.py` is finally the HTTP surface it should be — routes,
  validation, response shaping, app wiring — with every
  `_get_or_solve_*`/`_query_*`/`_advise*` orchestrator in `solving.py`,
  which imports no FastAPI and knows nothing about HTTP.
  - **The constants decision M61 deferred, now made the deep way:**
    constants are read as `cfg.X` **at call time from one canonical
    module**, never copied into any other module's namespace. M61 had to
    keep the orchestrators in `main.py` precisely because copies would
    have made `monkeypatch.setattr(api_main, X)` silently stop reaching
    the real reader. With one location, a patch reaches every reader —
    routes and orchestrators alike. **This is the change M61 named as
    requiring a real, deliberate test change**, and it did: 14 patch
    targets moved from `api_main` to `api_config`.
  - **A real bug the change introduced and the tests caught immediately
    — worth recording because it is a genuine hazard of this pattern:**
    `config` is a very common LOCAL name in this codebase (`config =
    GameConfig(...)`, `config = StreetConfig(...)`), so a module-level
    `from . import config` was silently shadowed inside exactly those
    functions, surfacing as `UnboundLocalError: cannot access local
    variable 'config'`. Fixed by aliasing to `cfg` (verified unused
    anywhere first). A qualified-module-reference refactor should always
    check the alias against local names — the collision is invisible
    until the shadowing function actually runs.
  - **Two more mechanical slips, both caught by running rather than
    reading:** the route section wasn't included in the first
    qualification pass (routes use constants as default argument values,
    evaluated at import), and `_ADVISE_UNSUPPORTED_CELLS` was rewritten
    to `api_config` by an ALL_CAPS regex before the solving-specific
    replacement could claim it. Neither could have been caught by
    inspection alone at this scale.
  - **Verification:** `python -m pytest tests/ -v` — 749 passed, zero
    regressions. Unlike M61, the test file DID change (14 monkeypatch
    targets plus three module references) — that was the known,
    deliberate cost of putting constants in one canonical place, named
    in advance in M61's own entry rather than discovered here.
  - **Audit recommendation #4 is now complete.** No frontend changes.

- **M63 — audit recommendation #5: 6/9-max iteration budgets.
  RESOLVED AS "DO NOT RAISE" — and the audit's own reasoning corrected.**
  The audit was explicit that this was investigation, not a dial to
  turn, because M27 had found that MORE iterations made convergence
  WORSE. Investigated exactly that way: measurement first, no change
  until the measurement said so.
  - **M27's finding survives, reproduced almost exactly.** Re-running
    M27's own experiment on current code — AFTER M33/M34's equity fixes,
    M48's evaluator rewrite and M55's memoization, any of which might
    plausibly have changed it — gives AKs's UTG-open fold rate as 15.6%
    (300 iterations) -> 48.7% (3k) -> **92.4%** (30k), against M27's own
    22.8 -> 69.2 -> 94.8. QQ goes 19.3% -> 86.2%; AKo 27.4% -> 96.6%.
    UTG folding QQ 86% of the time is not a strategy; it is divergence.
    **None of the intervening speedups or equity fixes touched the
    cause.**
  - **The audit's §6.1 was WRONG and is corrected in place, not quietly
    dropped.** It said "6-max at 300 iterations costs only 11s — there
    is real headroom", implying the budgets could simply be raised. Cost
    headroom is real; it was never the constraint. **300 is not a
    conservative budget — it is the count at which the answer is still
    sane.** `docs/project-audit-2026-08-21.md` now carries an explicit
    CORRECTION block in §6.1 and a resolved recommendation #5, so a
    future reader doesn't re-derive the same wrong conclusion from the
    original text.
  - **The forward-facing part, and why this milestone ships code at
    all despite changing no behavior:** a *characterization test*,
    `test_six_max_convergence_still_diverges_with_more_iterations`,
    pins the divergence. Deliberately asserting broken behavior, for two
    reasons: it turns the constraint behind `MULTIWAY_TABLE_CONFIGS`'
    budgets from a comment nobody executes into a runnable fact, and it
    gives whoever eventually fixes convergence a LOUD failure telling
    them the budgets can finally be raised. Its own docstring says so:
    "If this test fails, that is very likely GOOD NEWS."
  - **The real fix remains the one M27 named** and this milestone does
    not attempt: restructure CFR+'s regret update to mask out a hand's
    contribution for an iteration entirely rather than feed it any
    placeholder value — a genuine architectural change to
    `_mccfr_recurse`, not a tuning exercise.

    **Correction (M66): that was wrong.** The M27 fix named here was
    built and measured, and it did not change the divergence at all. The
    cause is `DEMO_MULTIWAY_HANDS` being 48.6% premium by combo weight,
    not anything in `_mccfr_recurse` — over a realistically-weighted pool
    the same solver is flat at 100x the budget. The characterization test
    above was renamed to
    `test_six_max_demo_pool_degrades_with_more_iterations` to say what it
    actually pins, and paired with a convergence test that is the real
    evidence. See M66's own entry.
  - **Verification:** `python -m pytest tests/ -v` — 750 passed, zero
    regressions (up from M62's 749 — 1 new). No production behavior
    changed; budgets are untouched by design.

- **M64 — audit recommendation #6: retire the surface `/advise`
  supersedes.** The user chose "deprecate the routes, retire the two
  superseded tabs" from four options laid out with measured tradeoffs.
  - **The audit's own count was imprecise, and checking it first changed
    the shape of the work.** It said "5 tabs superseded". Mapping each
    component to the endpoints it actually calls found three distinct
    groups, not one: **genuinely superseded** (Action-Path Wizard, Turn
    Advisor — same question, `/advise` does strictly more); **not
    superseded, different question** (Flop Solver / Multiway Flop Solver
    explore a board with a fixed curated demo range and no action path;
    Cached Flop Solver exists specifically to demonstrate the canonical
    library's hit/miss); and **genuinely distinct** (Preflop Ranges'
    169-cell grid, Equity Calculator). Only **2** tabs were actually
    superseded, not 5 — so only 2 were retired.
  - **One route was already orphaned and nobody had noticed:**
    `/solve_river_from_path` has no frontend consumer at all, reachable
    only via `/advise` or a direct API call.
  - **Routes deprecated, not deleted:** all five `*_from_path` routes
    carry `deprecated=True`, which flags them in the OpenAPI spec and
    strikes them through in `/docs` while leaving behavior completely
    unchanged — verified by reading `/openapi.json` back (5 flagged) and
    by their own test sections still passing untouched. External callers
    keep working; the direction is signalled rather than imposed.
  - **~1,300 frontend lines removed**, including 895 lines of tests
    whose coverage `AdviseSolver.test.tsx` already provides.
  - **Two `App.test.tsx` tests used the retired tabs as their examples**
    for hash-routing. That behavior is unchanged and still worth
    testing, so they were **repointed at surviving tabs rather than
    deleted** — losing routing coverage as a side effect of removing
    unrelated components would have been a silent regression in test
    quality.
  - **Verification:** `python -m pytest tests/ -v` — 750 passed, zero
    regressions. `npm test` — 145 passed (down from 169: the 24 removed
    are the two retired components' own tests, and no surviving test was
    deleted). `tsc --noEmit` clean.

- **M65 — audit recommendation #7: restructure `CLAUDE.md`.** The last
  item of `docs/project-audit-2026-08-21.md`'s list, and the one that
  created this file. **4,628 -> 423 lines** (91% smaller), with the
  4,363-line milestone log moved here.
  - **The argument that decided the shape:** `CLAUDE.md` is loaded into
    context at the start of *every* session. 4,282 lines of history were
    being paid for on every one of them, to serve a document that is
    consulted by search rather than read front-to-back. The audit
    suggested "add a current-state summary at the top"; that would have
    made the file *longer*. Splitting history out and making `CLAUDE.md`
    a lean current-state document does what the summary was for, and
    fixes the cost too.
  - **New "Current state (read this first)" section** — module map for
    both `poker_solver/` and `api/`'s four-layer chain, a
    "to change X, go to Y" table, the verification commands, and
    **"Known constraints — read before 'improving' these"**: the 6-max
    divergence (with the numbers), the multiway fidelity gap, why
    `trained`/`range_confidence`/`source` exist, and the deprecated
    routes. That section is aimed squarely at the failure mode this
    project keeps hitting — someone reasonably "fixing" something whose
    current form is load-bearing.
  - **Nothing was edited in the move**; entries are verbatim, only
    reordered strictly by milestone number (the append-only original had
    drifted out of sequence). Verified by counting milestone references
    before and after: **57 in the original, 57 now, none missing.**
  - **Corrections stay in-place by design**, and this file's header says
    so: where a later milestone disproved an earlier one, the earlier
    entry carries the correction (M9 corrected by M33, M54 by M55, M57
    by M63). Read an entry's corrections before trusting its conclusions.
  - **The workflow rules now say milestone entries go here**, not in
    `CLAUDE.md`, and that a milestone changing current state must update
    that section too — otherwise the split would decay back within a few
    milestones.
  - **Verification:** 750 backend + 145 frontend tests pass. This
    milestone changed only documentation.

  **This completes all seven of the audit's recommendations (M58-M65).**

- **M66 — The 6-max "convergence defect" is a hand-pool artifact, not a
  solver bug.** The longest-standing open correctness question in the
  project: `?players=6` and `?players=9` ship with a 300-iteration budget
  (against 3-max's 100,000) because more solving made the answer *worse*
  — AKs's UTG-open fold rate climbing 15.6% -> 48.7% -> 92.4% across
  300 / 3k / 30k iterations. M27 diagnosed a cause and named a fix but
  scoped it out; M63 confirmed the effect was still live on current code.
  This milestone implemented M27's named fix, measured it, found it was
  **not** the cause, and then found what actually is.
  - **M27's named fix, built and measured:** "restructure CFR+'s regret
    update to mask out a hand's contribution for an iteration entirely
    rather than feed it any placeholder value." Implemented in three
    parts. `MultiwayEquityCache` gained `traverser_validity_mask` — a
    boolean companion to `traverser_equity_vector` recording, per
    candidate, whether that entry came from a real simulated showdown or
    from `_pairwise_fallback_equity`; it is filled in the same pass and
    cached under the same key, so it costs nothing extra.
    `_mccfr_terminal_value` folds that mask into NaN, unifying it with
    the representation `NwayBoardEquityCache` already uses natively
    (M30's "no placeholder value, ever" convention), and **stops**
    `nan_to_num`-ing to 0.5 (M32's choice, deliberately reversed here).
    `_mccfr_recurse` then skips the regret and `strategy_sum` updates for
    any hand whose value is non-finite.
  - **A design note worth recording, because it made the change much
    smaller than expected:** no signature anywhere needed to change. NaN
    propagates conservatively for free — every arithmetic step from
    terminal to decision node is per-hand (`einsum("ha,ha->h")` contracts
    over actions, never over hands), so a NaN for hand h taints h's value
    at every ancestor and touches no other hand. That is exactly the
    desired semantics: if *any* line reachable this iteration priced h
    with a fabricated number, h's regret is untrustworthy everywhere
    above, not only at the terminal where the fabrication happened. It
    also survives a zero strategy weight, since `0 * NaN` is still NaN,
    so a tainted action cannot hide behind an action the strategy has
    already abandoned.
  - **Result: the divergence was unchanged.** AKs still ran 25.2% ->
    67.8% -> 94.5%. Reported as a negative result rather than quietly
    reframed, matching M18's own precedent for a predicted speedup that
    did not materialize.
  - **The actual cause, found by questioning the setup instead of the
    solver: `DEMO_MULTIWAY_HANDS` is 48.6% premium by combo weight**
    (AA/KK/QQ/AKs/AKo out of 8 classes). At 6-max the traverser faces
    five opponents each drawn from that pool, so **~97% of the time at
    least one holds a premium hand** — and under those conditions folding
    AKs under the gun genuinely is close to correct. The solver was never
    diverging. It was converging, correctly, to the right answer for a
    game nobody meant to ask about. At 3-max only two opponents draw from
    the same pool (~74%), which is why 3-max looked fine at 100,000
    iterations and 6-max did not.
  - **Measured, with a control, not asserted:** diluting to 34 classes /
    10.2% premium makes AKs's UTG fold rate **2.5% -> 1.2% -> 1.7%**
    across 300 / 3k / 30k — flat at 100x the shipped budget, where the
    demo pool climbs to 94.5%. QQ likewise 1.3% -> 0.6% -> 0.7%. Because
    that pool changed *two* variables at once (density and size), a
    control was run at the demo pool's own size (8 classes) but
    premium-light: it **still degraded** (KK 1.4% -> 1.6% -> 23.6%). So
    density is the dominant factor but pool coarseness contributes too,
    and the honest conclusion is that the instability is a property of
    the demo pool being both small and premium-heavy — not of the CFR
    implementation. Stated that way rather than as the cleaner
    density-only story the first experiment alone would have supported.
  - **The masking change shipped anyway, on its own merits, with its
    failure to fix 6-max stated plainly.** A/B'd in the regime where
    correctness is actually measurable (the diluted pool, which
    converges): at 3,000 iterations the two arms are identical (AA
    0.1%/0.1%, AKs 1.2%/1.2%, QQ 0.6%/0.6%) at identical cost (116.7s vs
    115.8s), and the full suite runs 229.7s against a 230.2s baseline. So
    it changes nothing well-determined and costs nothing — it only
    differs where neither answer was trustworthy. What it buys: hands are
    no longer trained on fabricated numbers, and a hand that never once
    had a real value now correctly reports `trained_mask() == False`
    (M28) instead of carrying a placeholder-derived strategy.
  - **Why this matters more than a tuning fix would have:** CFR+ floors
    regret at zero and never lets it decrease, so any persistent
    one-sided bias accumulates forever instead of averaging out. That
    ratchet is why "feed it a neutral value" is not a safe substitute for
    "skip the update", and it is the reason M27's instinct was sound even
    though the specific culprit was not.
  - **Tests:** `test_cfr.py` — the M32 test asserting `nan -> 0.5` was
    rewritten to assert NaN is now *preserved* (the behaviour it
    deliberately replaces), plus new tests that a validity mask is folded
    into NaN, that an unpriceable hand leaves `regret_sum`/`strategy_sum`
    untouched and reports untrained, that NaN never enters either
    accumulator, and — the necessary other half — that a hand which *is*
    priceable still learns normally, so masking cannot "fix" divergence
    by refusing to learn at all. `test_equity.py` — four tests for
    `traverser_validity_mask` (a blocked candidate, an all-clear pool,
    the all-false mutually-conflicting-opponents case, and that asking
    for mask-then-vector or vector-then-mask gives identical answers with
    only one entry computed). `test_solver.py` — the characterization
    test was **renamed and rewritten**:
    `test_six_max_convergence_still_diverges_with_more_iterations` is now
    `test_six_max_demo_pool_degrades_with_more_iterations`, documenting
    that it pins a property of the *pool*, not a solver defect, and it is
    paired with a new `test_six_max_converges_with_a_realistic_pool` that
    is the actual evidence. The new test uses the cheapest configuration
    found that still shows the effect (14 classes / 23.9% premium, ~35s)
    rather than the 34-class version's ~2.5 minutes.
  - **Budgets left at 300, but the reason changed.** It is no longer
    "MCCFR is broken at 6-max"; it is "this pool makes large counts
    meaningless." Corrected in place in `api/config.py`,
    `docs/project-audit-2026-08-21.md` (§6.1 and recommendation #5, whose
    "the real fix remains the one M27 named" conclusion this milestone
    disproves), and CLAUDE.md's known-constraints section.
  - **Verification:** `python -m pytest tests/ -v` — full suite green.
    No frontend files touched.
  - **What this unlocks, and what it defers:** replacing
    `DEMO_MULTIWAY_HANDS` with a larger, realistically-weighted pool is
    now the identified path to raising the 6-max/9-max budgets. It is
    deliberately *not* done here — it changes every multiway endpoint's
    output and costs 3-9x more per solve at these table sizes, so it
    needs its own cost-and-budget measurement pass. That is a product
    change with a clear brief, which is a much better place to be than an
    open-ended solver investigation.

- **M67 — Multiway preflop solves the real game (all 169 classes), and
  the honest limits of doing so.** M66 named the demo hand pool as the
  next thing to fix. Scoping it turned up something worse than the
  convergence issue and that M66 had not looked for: **multiway preflop
  advice did not exist for ~95% of starting hands.** A 6-max request
  holding T7s or 98s returned HTTP 200 with `strategy: null` — and
  `in_range: true` beside it. This milestone closes that, and in doing so
  measured several things that contradict what was believed going in.
  - **The pool is now `MULTIWAY_PREFLOP_HANDS = all_starting_hands()`**,
    the same 169-class set heads-up has always used; premium density
    drops from 48.6% to a real 2.6%. The old 8-class list moved to
    `tests/test_api.py` as `FAST_MULTIWAY_HANDS`, monkeypatched by the
    autouse fixture — those tests assert shapes and status codes, never
    strategy values, so a curated pool is harmless there and keeps
    `tests/test_api.py` at ~92s.
  - **`in_range` was lying, and now isn't.** `_advise_preflop` hardcoded
    `hero_in_range: True`, reasoning that "a preflop solve covers every
    class, so there's no cap for hero to fall outside of." True at
    heads-up, false at multiway, and nothing had ever exercised the
    difference. Now derived (`hero_key in strategy`), so it stays correct
    under any future pool restriction instead of by coincidence. Pinned
    by a pair of tests — one asserting False for an out-of-pool hand, one
    asserting the signal doesn't simply always say False.
  - **300 iterations is not enough over 169 classes — a NEW failure,
    opposite to the old one.** Pre-M67 the danger was too MANY
    iterations; here too few produce flatly wrong fold rates (T7s folding
    22.6% under the gun at 6-max). Caught only by checking the action mix
    rather than the fold rate alone: at 300 iterations AKs's fold rate
    looked fine at 2.6% while it was jamming 100bb **62.9%** of the time,
    against 3.1% for the trusted heads-up solve. **Fold rate alone is not
    a sufficient convergence check** — it hides everything happening
    among the non-fold actions.
  - **The fix was budget plus sample count, measured as a trade.**
    `MULTIWAY_PREFLOP_SAMPLES = 50` (below the engine's own 200) because
    with 169 classes the binding constraint is iterations, not sample
    precision — cheaper evaluations buy ~6x the iterations for the same
    wall clock, and that is the axis that matters. Measured at 6-max:
    | setting | T7s fold | 72o fold | AA jam | cost |
    |---|---|---|---|---|
    | samples=200, 300 it | 22.6% | 94.4% | 65.7% | ~170s |
    | samples=50, 3,000 it | 69.8% | 98.3% | 22.3% | 325s |
    | samples=50, 10,000 it | 86.9% | 98.7% | 19.4% | 712s |
    Budgets set to 3,000 (3-max and 6-max) and 1,000 (9-max). 3-max drops
    from 100,000, which had been tuned against a pool where an iteration
    was ~20x cheaper. **9-max's 1,000 is EXTRAPOLATED** from a measured
    79.4s at 300 iterations, not measured directly — flagged rather than
    presented as measured.
  - **A deliberate, documented limitation.** The split among the NON-fold
    actions is still not converged even at 10,000 iterations (AA jams
    ~19% where a converged solve puts it near 0). Multiway preflop output
    is therefore trustworthy for "is this hand playable from this seat"
    and NOT for "which sizing". Shipped with that stated in
    `api/config.py` and CLAUDE.md rather than left for a user to
    discover, because the existing honesty signals do NOT catch it —
    `trained` is True for all 169 classes, since it only reports that a
    hand received updates, not that those updates converged.
  - **Two speed attempts, both honestly negative, both kept.** Profiling
    a 6-max 169-class solve pointed hard at `Card.value` (39M calls,
    recomputing a string lookup on a frozen dataclass whose rank never
    changes) and `hand_utils.rank_value` (42.5M calls doing `.upper()` +
    `in` + `.index()`). Both were fixed — `Card.value` precomputed once in
    `__post_init__`, `rank_value` switched to a dict — and
    `_simulate_equity`'s inner loop was changed to sample deck INDICES and
    gather from two numpy arrays instead of rebuilding 5-element Python
    lists of `Card` objects per sample. **Wall clock: 171.5s against a
    166-174s baseline. No measurable gain.** Exactly M47's trap repeated:
    under cProfile every Python call is inflated, so the highest-*count*
    functions look dominant while the real cost is numpy hand-evaluation
    volume. Kept anyway — all three are strictly better code and the
    `_simulate_equity` change was verified **bit-identical** (equity
    vectors compared before/after; `random.sample` over `range(len(deck))`
    consumes the RNG in exactly the same sequence, which matters because
    `_stable_seed` exists to guarantee cross-process reproducibility).
  - **One speed change that did help, and is a real root fix:**
    `MultiwayEquityCache` instances are now shared across solves via
    `_multiway_equity_caches`, keyed by the hand pool. Preflop equity is a
    property of the HANDS alone — not stack depth, and not table size
    beyond the opponent-tuple length the cache already keys by — so
    building a fresh one per `(stack, players)` spot discarded every
    simulated equity the moment a different stack was requested. Measured
    at 6-max: 173.0s cold, then 128.0s / 111.6s for further stack depths
    (~30%, less than hoped because each solve's RNG diverges and draws
    partly different opponent tuples). Keyed by the pool rather than held
    as a module-level instance specifically because the test fixture
    monkeypatches the pool after import — a single instance would have
    held 169 hands while the solve ran over 8, a silent length mismatch.
  - **The architectural finding, which is the real answer.** Heads-up is
    both fast and converged because it solves exactly (CFR+) against a
    precomputed, disk-cached 169x169 equity table. Multiway has no such
    table: `MultiwayEquityCache` Monte-Carlo-simulates equity per opponent
    tuple, and with 5 opponents drawn from 169 classes almost every
    iteration is a fresh tuple, so iteration cost never amortizes. That —
    not the hand pool, not the regret update, not per-call Python overhead
    — is why multiway preflop is slow, and no amount of budget tuning
    fixes it. **The next milestone is a precomputed multiway equity
    structure**, the direct analog of what already makes heads-up work.

    **Correction (M68): that recommendation was wrong.** It was tried and
    measured. The tuple space cannot be collapsed without losing
    hero-opponent interaction (domination, blockers): pairwise-derived
    estimators reach correlation as low as 0.39 at 9-max, and bucketing
    opponents by strength plateaus at ~3x the Monte Carlo noise floor no
    matter how many buckets are used. The real inefficiency was
    elsewhere — the same opponent hands were being re-ranked once per
    candidate — and fixing that gave 1.95x. See M68.
  - **Two caps that had never actually bound, now measured.** M54 set
    `MAX_MULTIWAY_PATH_QUERY_CLASSES_PER_POSITION` and its turn sibling to
    6, noting both were free *only* because the preflop leg solved 8
    classes, and left an explicit caveat that a wider path "would cost
    more than 17.03s", unmeasured. They bind now. On a real 6-max
    open/call/call path reaching a genuine 3-live-position flop, preflop
    leg pre-warmed and excluded: **flop cap=2 -> 3.1s, cap=4 -> 6.6s,
    cap=6 -> 11.5s; turn cap=2 -> 0.6s, cap=4 -> 1.0s, cap=6 -> 1.5s.**
    The caveat was pessimistic — a genuinely binding cap=6 costs *less*
    than M54's own non-binding reading. Not like-for-like (M54's path was
    3-max-origin), and recorded that way. Both left at 6, now on measured
    rather than incidental grounds.
  - **Verification:** full backend suite green. No frontend files
    touched — nothing there depended on the pool's size.

- **M68 — Multiway equity gets ~2x faster by sharing board runouts; the
  precomputed-table idea M67 recommended does not work.** M67 closed by
  naming its own next milestone: "a precomputed multiway equity
  structure, the direct analog of what already makes heads-up work."
  This milestone tried exactly that, measured it, and found it doesn't —
  then found the real inefficiency somewhere else entirely.
  - **Why a precomputed table can't work, measured three ways.**
    Heads-up tabulates 169x169 pairwise equities to disk. The multiway
    analog needs hero's equity against a *multiset* of opponents, and
    169^5 is not tabulable, so the question is whether the tuple space
    can be collapsed.
      - *From pairwise equities.* Using the existing precomputed table:
        `mean(p_i)` — which is what `_pairwise_fallback_equity` actually
        does today — is biased **+0.18 / +0.32 / +0.39** at 2 / 5 / 8
        opponents. It massively overstates. `prod(p_i)` (independence)
        underestimates as board correlation predicts (-0.06 / -0.13 /
        -0.10). A normalized product is nearly unbiased (+0.01 to +0.02)
        but has MAE 0.08-0.14 and correlation as low as **0.39** at
        9-max — it barely ranks hands correctly.
      - *By bucketing opponents on strength.* Collapses the tuple space
        to a tabulable 169 x C(B+k-1, k) while keeping each entry a real
        simulation, so board correlation survives. Measured MAE
        0.054-0.084 — and **more buckets did not help**, which is the
        tell.
      - *The tell, chased down.* Bucketing was on heads-up strength, and
        heads-up strength is the wrong axis: correlation with true
        multiway strength is only 0.885, and the disagreements are
        exactly what poker theory predicts — suited hands are
        systematically **under**rated (54s +56 rank places, 53s +52, 42s
        +44: they make flushes, which win multiway pots) and small pairs
        and weak aces **over**rated (55 drops 74 places, A7o 73). Redoing
        the bucketing on a directly-measured multiway strength improved
        bias (+0.009 vs +0.024) and correlation (0.657 vs 0.458) but left
        MAE at ~0.068 — still **3x the measured noise floor of
        0.019-0.023**. The residual is hero-opponent *interaction*:
        domination and blockers (AKs against a dominated AQ that also
        blocks hero's ace is nothing like AKs against 76s of equal
        "strength"), which no summary of opponent strength can carry.
    **So M67's recommendation was wrong, and is corrected in place there.**
  - **The real inefficiency, found while measuring the above.**
    `traverser_equity_vector` called `_simulate_equity` once per
    candidate, and each call drew its own boards and ranked *all* hands
    on them — so the k opponents' hands were re-ranked once per
    candidate. At 169 candidates against 5 opponents that is
    `169 * samples * 6` hand evaluations where `samples * (169 + 5)`
    suffice: **the same 5 opponent hands were being evaluated 169 times
    over.** New `_simulate_equity_shared_board` draws one set of boards
    and evaluates every candidate and every opponent against them, making
    the opponents' cost O(1) in the candidate count instead of O(n).
  - **Measured payoff:** a 6-max 169-class solve at 3,000 iterations goes
    **325s -> 166.5s (1.95x)**; at 300 iterations 46.5s -> 26.4s.

    **CORRECTION (M70): withdraw the 1.95x.** Those two numbers were
    measured in different sessions, and this machine was later observed
    running the same workloads ~1.7x slower than when they were taken, so
    the ratio cannot separate the optimization from machine drift.
    Re-measured controlled (one process, interleaved): the shared-board
    change is **6.06x at the equity layer**, matching its own structural
    prediction. Bigger than claimed, but the end-to-end figure was not a
    valid measurement. Since
    the budgets are cost-bound, this converts directly into convergence:
    **12,000 iterations now costs 281s, less than M67's 3,000 cost
    (325s)**, and T7s's UTG fold rate improves from 69.8% to 87.4%.
  - **Accuracy checked, not assumed.** The shared-board estimator is
    *not* bit-identical to the per-candidate loop and cannot be — it
    draws a different number of boards in a different order. So it was
    validated against high-sample ground truth instead: bias **-0.0065**,
    MAE **0.0494** against a 4,000-sample truth, which is exactly what
    pure sampling noise looks like at `samples=50` (SE ~0.053). No
    systematic bias. The one real subtlety is that boards are drawn from
    a deck excluding only the *opponents'* cards, so ~22% of samples
    collide with a given candidate's own cards; those samples are skipped
    for that candidate. That costs precision, not bias, because which
    boards collide depends only on the candidate's cards, not on how it
    performs.
  - **A latent order-dependence bug, surfaced and fixed.**
    `_pairwise_fallback_equity` drew from `rng` once per opponent while
    iterating the *caller's* order, even though `MultiwayEquityCache`'s
    key has always been sorted — so the same situation described as
    (AKs, T9o, KK) or (KK, T9o, AKs) returned 0.690 and 0.700.
    `test_multiway_cache_is_order_independent_across_fresh_caches` passed
    before only because the two orders happened to coincide at the rng
    state that test reached; changing how much rng is consumed upstream
    exposed it. Now iterates sorted, so the property holds by
    construction. **Worth noting this was pre-existing** — the M68 change
    surfaced it rather than caused it.
  - **Budgets re-set on measured cost, replacing M67's extrapolation.**
    3-max 12,000 (48.3s), 6-max 12,000 (281s), 9-max 3,000 (248.9s).
  - **A new honest limitation, measured: 9-max preflop output is not
    reliable.** T7s folds only **12.5%** under the gun at a 9-handed
    table, where it should be near 100% and 6-max reaches 87.4%. With 8
    opponents the sampled-opponent variance is high enough that 3,000
    iterations is proportionally far less converged than the same count
    at 6-max, and per-iteration cost (~83ms) puts a converging count out
    of reach. Documented in `api/config.py` and CLAUDE.md as the least
    trustworthy cell in the product rather than left to be discovered.
  - **M67's sizing limitation persists and is now known to be
    structural.** Fold rates converge steadily with iterations (T7s
    66.4% -> 80.1% -> 87.4% at 3k/6k/12k) but AA's jam frequency
    *wanders* (33.4% -> 35.5% -> 25.3%) instead of trending toward the
    near-zero a converged solve gives. More iterations will not fix the
    sizing axis.

    **Partly explained and improved (M69):** one real cause was that the
    time-average weighted every iteration equally, so the untrained
    opening iterations — whose `current_strategy()` is exactly uniform —
    never washed out. Linear averaging cuts AA's jam from 25% to 20% and
    lifts T7s's UTG fold from 87% to 94% at the same cost. The axis is
    improved, still not resolved.
  - **Verification:** 762 backend tests pass. Two new tests pin the
    shared-board estimator's contract (statistical agreement and
    order-independence). No frontend files touched.

- **M69 — Linear averaging: the multiway strategy average was
  contaminated by its own untrained opening iterations.** M67 shipped a
  documented limitation (multiway preflop is trustworthy for
  fold-vs-play, not for sizing) and M68 established it was structural
  rather than budget-bound — AA's jam frequency *wandered* (33% -> 36%
  -> 25% at 3k/6k/12k) instead of trending toward the ~3% a converged
  solve gives. This milestone found a real cause and fixed part of it.
  - **The tell was in the numbers themselves.** At 12,000 iterations AA's
    mix was jam 0.253 / call 0.245 / raise 0.502 — two of four actions
    sitting at almost exactly 0.25. `InfoSetTable.current_strategy()`
    returns an *exactly* uniform `1/num_actions` while regrets are still
    zero, and `strategy_sum += reach * strategy` weighted **every
    iteration equally**. So iteration 1's untrained uniform guess counted
    as much as iteration 12,000's converged one, and a long run's average
    never escaped it. Not a subtle numerical issue — a missing standard
    CFR+ practice.
  - **The fix:** `mccfr_solve(linear_averaging=True)` (now the default)
    weights iteration t's contribution by t, via a new `strategy_weight`
    threaded through `_mccfr_recurse`. Applied ONLY to `strategy_sum` —
    regret updates and therefore the sampled traversal are untouched, so
    for a given seed both settings walk the identical tree.
  - **Measured on a real 6-max 169-class solve, at identical cost** (the
    change is one scalar multiply; 183s vs 179s at 3k, 481s vs 481s at
    12k):
    | iterations | weighting | AA jam | T7s UTG fold | 72o fold |
    |---|---|---|---|---|
    | 3,000 | equal | 0.33 | 0.66 | 0.98 |
    | 3,000 | **linear** | **0.26** | **0.78** | 0.99 |
    | 12,000 | equal | 0.25 | 0.87 | 0.99 |
    | 12,000 | **linear** | **0.20** | **0.94** | 0.99 |
    Every figure moves toward the truth, and linear at 3,000 beats equal
    at 3,000 by roughly what quadrupling the iterations would buy.
  - **It does NOT fully fix the sizing axis, and that is stated rather
    than glossed:** AA still jams 20% where a converged solve is near 3%.
    A real improvement on a known problem, not a resolution of it. The
    fold-vs-play axis, which is what multiway advice is actually used
    for, is now in good shape at 6-max (T7s folds 94% under the gun).
  - **A test premise that was wrong, caught by writing the test.** The
    intuitive assertion — "linear averaging moves the average further
    from uniform" — is false in general, and measured false on the very
    fixture written to check it (spread 0.859 vs 0.996). Replaced with
    the property that is actually exact: because `strategy_weight` never
    touches regrets, both runs traverse identically, so the linearly
    weighted `strategy_sum` must exceed the equally weighted one and
    cannot exceed it by more than a factor of `iterations`. The
    behavioural claim lives on the 6-max measurements above, where it
    belongs, not in a toy unit test that cannot support it.
  - **A false alarm worth recording, because it nearly produced a wrong
    conclusion.** The full suite measured 425s after this change against
    M68's recorded 212s — an apparent 2x regression. Isolating it showed
    `tests/test_cfr.py` was unchanged (35.70s vs 35.41s), and re-running
    the suite on clean M68 `main` gave **429.69s**. So there was no
    regression at all: **M68's 212s was the outlier**, a single reading
    that should not have been recorded as a baseline. The same lesson
    M49 and M54 each learned separately in this project — one reading is
    not a measurement.
  - **Verification:** 764 backend tests pass. Two new tests pin the
    mechanism and the off-switch. No frontend files touched.

- **M70 — Stop computing values that are then thrown away; and a
  correction to M68's own headline number.** Two open defects went into
  this milestone (9-max unreliability, the sizing axis) and neither came
  out fixed — but chasing them turned up a 1.38x speedup, a measurement
  methodology this project needed, and a published figure that was wrong.
  - **M68's "1.95x" was not a trustworthy number, and is corrected.** It
    compared 325s (measured during M67) against 166.5s (measured during
    M68) — different sessions. This milestone observed the same
    workloads running **~1.7x slower** than when M68 measured them
    (9-max/3,000 iters: 418s here vs 249s there; 6-max/12,000: 491s vs
    281s), so a cross-session ratio cannot separate the optimization from
    machine drift. Re-measured **controlled** — both implementations, one
    process, interleaved, same opponent tuple — the shared-board change
    is **6.06x at the equity layer**, matching its own structural
    prediction of ~5.8x fewer hand evaluations. The optimization is real
    and larger than claimed; the end-to-end 1.95x figure is withdrawn.
  - **The methodology fix, which this project needed more than the
    speedup:** absolute wall-clock timings recorded in different sessions
    are not comparable here. Every timing claim from now on should either
    be an interleaved A/B in one process, or be normalized against a
    fixed reference workload measured in the same run (M70's own speed
    script does the latter, reporting "reference units" alongside
    seconds). Recorded in CLAUDE.md, because this log is full of absolute
    numbers that later milestones compare against.
  - **The real find: ~30% of a solve was computing values M66 already
    discards.** Profiling the current 6-max 169-class solve showed
    **821,100 calls to the SCALAR `hand_eval.rank_five` path** — not the
    vectorized one M48 built. They came from `_pairwise_fallback_equity`,
    which ran a fresh 50-sample Monte Carlo per opponent. But since M66,
    `cfr._mccfr_recurse` masks exactly those entries out of the regret
    and strategy updates, so the number was never learned from.
  - **The fix is a lookup, and it is strictly better on three axes.**
    `_pairwise_fallback_equity` now reads the precomputed 169x169
    pairwise table. It is the *same* quantity by definition; the table is
    built at 200 samples against the fallback's own 50, so the lookup is
    the **more** precise estimate, not a cheaper approximation; and it
    consumes no `rng` at all, which retires by construction the entire
    order-dependence bug class M68 had to fix by sorting.
    **Measured controlled (one process, interleaved, with a reference
    workload): 49.8s -> 36.1s on a real 600-iteration 6-max solve, 1.38x.**
  - **9-max is under-trained, not broken — confirmed, and the cost of
    fixing it is now known.** `traverser = positions[iteration % len(
    positions)]` divides iterations among seats, so 6-max at 12,000 gives
    2,000 per position while 9-max at 3,000 gives 333 — 6x less. Tripling
    9-max's budget moved T7s's UTG fold rate **0.117 -> 0.301** (3,000 ->
    9,000 iterations), confirming it responds to training rather than
    being structurally wrong. But 9,000 iterations cost 1,241s, and
    reaching 6-max's per-position parity needs ~18,000 (~40 min/spot) —
    and even that would likely fall short, since T7s should fold *more*
    at 9-max than 6-max's 0.94, not less. Left at 3,000 and still
    documented as the least trustworthy cell.
  - **`EXPLORATION_EPSILON` is NOT the sizing culprit.** The hypothesis
    was that a 0.05 exploration floor makes opponents call jams more
    often than they should, inflating the jam's apparent value. Measured
    at 6-max/12,000: dropping it to 0.01 cut AA's jam 0.201 -> 0.169 but
    *raised* AKs's 0.218 -> 0.264. Mixed, not a clean mechanism, so
    epsilon is left at 0.05 rather than tuned on an ambiguous result.

    **Confirmed refuted (M73), on stronger evidence.** Epsilon 0.002 at
    12,000 iterations looked like a clean fix on one seed (AA jam 0.024,
    AKs 0.034) and then gave 0.024 / 0.211 / 0.516 across three. Do not
    re-tune epsilon for this.
  - **A test that pinned the implementation, not the contract.**
    `test_pairwise_fallback_equity_matches_monte_carlo_equity_for_one_
    opponent` asserted exact equality with a specific 50-sample Monte
    Carlo call at a specific rng state. That is the implementation, not
    the behaviour, and it broke on a change that made the value *more*
    accurate. Rewritten to assert the contract — the fallback is that
    hand's pairwise equity — plus a new test that it consumes no
    randomness at all.
  - **Verification:** 765 backend tests pass. No frontend files touched.
  - **Budgets deliberately left alone.** They were set in M67/M68 on
    absolute timings now known to be session-dependent. Re-tuning them on
    that basis would compound the error; they should be re-validated with
    the reference-workload method before being changed again.

- **M71 — CFR+'s regret clamp was the sizing bug, and the "trusted"
  heads-up reference was contaminated too.** M67 shipped multiway preflop
  with a sizing caveat, M68 found it structural, M69 improved it via
  linear averaging but left AA jamming ~20% where a converged solve is
  ~3%. This milestone found the actual cause, and then found that the
  reference used to judge everything all session had the same class of
  defect.
  - **The mechanism:** CFR+ clamps accumulated regret at zero. That is a
    genuine win in an exact solver, but under SAMPLING it is a ratchet —
    it discards negative regret while accumulating positive regret, so
    whichever action's value estimate is noisiest collects spurious
    positive regret that can never be cancelled. The all-in is by far the
    noisiest action, since its payoff swings an entire stack. That is
    exactly where the bias appeared, and it is why more iterations did
    not help: the ratchet accumulates *with* iterations.
  - **Measured at 6-max, 169 classes, 3,000 iterations, three seeds each**
    (heads-up reference for AA's jam is ~3.1%):
    | | AA jam | T7s UTG fold |
    |---|---|---|
    | CFR+ clamp (old) | 0.211 / 0.203 / 0.182 → **0.199** | 0.744 |
    | plain CFR (new) | 0.034 / 0.033 / 0.029 → **0.032** | 0.938 |
    Reproducible and tight, not a lucky seed. Plain CFR at 3,000
    iterations beats CFR+ at 12,000 on every metric.
  - **Published Discounted CFR was tried and was worse.** DCFR(1.5, 0)
    gave AA jam 0.139 and DCFR(1.5, 0.5) gave 0.103, against plain CFR's
    0.034 — at roughly twice the cost, since discounting walks every
    table each iteration. `discount=(alpha, beta)` is kept as an option
    but is not used. The textbook fix lost to simply removing the clamp.
  - **One measured exception, and it is 9-max.** Plain CFR converges more
    slowly than CFR+, so it needs enough iterations per position to get
    there — and `traverser = positions[iteration % len(positions)]`
    divides iterations among seats. 6-max at 3,000 gives 500 per
    position and 3-max at 12,000 gives 4,000; both win big (3-max's AA
    jam 0.468 → 0.120). **9-max at 3,000 gives only 333 per position and
    goes the wrong way** (AA jam 0.777 → 0.982, three seeds), even as its
    T7s fold improves. So `api/config.py` keeps 9-max on the CFR+ clamp
    explicitly and says why. One more reason 9-max is the weakest cell.
  - **A single reading nearly produced the wrong conclusion, again.** The
    first 3-max and 9-max runs (one seed, at 3,000 iterations rather than
    their configured budgets) both showed plain CFR looking *worse*,
    which contradicted 6-max and would have sunk the change. Re-running
    with three seeds at the real budgets reversed 3-max entirely and
    confirmed only 9-max as a genuine exception. Same lesson as M49, M54
    and M70 — four times in this project now.
  - **The bigger find: the exact heads-up solver had M69's defect too.**
    Removing the clamp broke
    `test_mccfr_agrees_with_exact_solve_at_heads_up`, where the exact
    solver said AA jams 0.656 and the sampled one said 0.977. Rather than
    assume the exact solver was right because it is unsampled, its
    convergence was measured: **0.656 → 0.892 → 0.956 → 0.969 at 500 /
    2k / 10k / 50k iterations.** The 0.656 was not the equilibrium — it
    was the untrained opening iterations still weighing in the average,
    because `solve()` also did `strategy_sum += reach * strategy` with
    equal weighting. **The sampled solver was right and the reference was
    wrong.** Linear averaging now applies there too (0.765 / 0.958 /
    0.972 / 0.973 — the same equilibrium, reached far sooner).
    This matters beyond the test: every "trusted heads-up reference"
    figure quoted across M67-M70 came from this solver.
  - **Three tests fixed, each for a real reason rather than to go green:**
    the cross-validation now runs its exact arm to 5,000 iterations
    (a cross-check is meaningless until *both* arms converge, or it just
    measures the reference's error); the blocked-board test no longer
    hardcodes "BB's top class is the pair 22" (M71's reordering made it
    A2, which no 3-card flop can block) and instead derives the pair and
    asserts that premise explicitly; and the demo-pool characterization
    test no longer claims 300 is "the shipped budget", which stopped
    being true at M67.
  - **Verification:** 765 backend tests pass. `_solve_recurse`'s CFR+
    clamp is deliberately UNCHANGED — the clamp is only harmful under
    sampling, and the exact solver does not sample.

- **M72 — An end-to-end check found that M71 shipped a configuration it
  never measured.** Five milestones (M66-M71) changed solver defaults —
  the regret clamp, linear averaging in both solvers, the equity
  fallback, shared board runouts. Every one was validated by unit tests
  and by targeted measurements. Nothing had exercised the actual product
  surface since. This milestone did, and immediately found a real defect
  in what was already on `main`.
  - **The defect.** M71 removed CFR+'s regret clamp after validating it
    at **3,000** iterations (AA's jam 0.199 -> 0.033, three seeds). The
    shipped 6-max budget was **12,000**, and that point was never
    re-measured. Without the clamp AA's jam frequency GROWS with
    iterations — measured, two seeds each: **0.033 at 3k -> 0.149 at 6k
    -> 0.404 at 12k.** So at the budget actually shipped, M71's change
    was *worse* than the thing it replaced (clamped 12,000 gave ~0.20).
    A correct finding, applied at an unvalidated operating point.
  - **How it was caught, and why nothing else caught it.** A direct
    `/advise` check at production settings, asserting poker-sane
    properties that hold regardless of solver internals: AA never folds,
    72o folds a lot from early position, a set continues more than air.
    AA-folds and T7s-folds passed; **AA-jams-under-15% failed.** No unit
    test covers this, because the suite's fixtures shrink pools and
    iteration counts for speed — which is correct for testing plumbing
    and exactly why it cannot see a budget-dependent solver property.
  - **The fix: each table size's budget set from its own measurement.**
    6-max drops 12,000 -> **3,000**, which is simultaneously the best
    measured point on both axes and the cheapest (AA jam 0.033, T7s UTG
    fold 0.963, 133s against 309s). 3-max **keeps 12,000**, because it
    measured the opposite way (AA jam 0.527 at 3,000 vs 0.120 at 12,000,
    three seeds) and costs only 48s. The two table sizes genuinely
    disagree about the right budget; pretending otherwise is what caused
    this.
  - **Product effect, not just a metric.** At 6-max, AA's top action goes
    from `call_or_check` to `raise:2.50` — the correct GTO action. The
    end-to-end check now passes on every assertion, and the 6-max solve
    is 137.7s rather than 203.6s.
  - **A regression test at the SHIPPED budget**, which is the part that
    was missing:
    `test_six_max_jam_frequency_at_the_shipped_budget` reads
    `MULTIWAY_TABLE_CONFIGS[6]` and asserts the property there rather
    than at a convenient count. It costs ~133s, which is a real addition
    to a ~360s suite and is accepted deliberately: the bug it guards
    reached `main` and would have stayed. If someone raises the budget,
    this fails, and the failure message says to re-measure.
  - **The honest framing.** This is the same error this project has now
    documented four times in other forms (M49, M54, M70, M71) wearing a
    new disguise: not "one reading is not a measurement" but **"a
    measurement at one operating point is not a measurement at the
    shipped one."** M71's conclusion was right; its application was not
    checked where it mattered.
  - **Open, and now precisely stated:** without the clamp, jam frequency
    grows with iterations at 6-max. 3,000 is a measured-best operating
    point, not a stable property — the underlying cause is likely
    `current_strategy()` falling back to a UNIFORM distribution when
    every action's regret is negative, which puts real weight on the
    all-in. That fallback is the natural next thing to investigate.

    **Correction (M73): refuted.** The all-negative fraction is ~67-71%
    in every arm and DECREASES with iterations, nearly identical clamped
    vs unclamped — it is dominated by rows that were never visited (~70%
    of all rows), not by rows that went negative. The exploration floor
    was tested too and also refuted. The instability at 12,000 is real
    but none of the three suspected causes explain it.
  - **Verification:** full backend suite green; end-to-end `/advise`
    check passes at production settings across heads-up preflop/flop and
    6-max preflop.

- **M73 — Two hypotheses for the jam instability, both refuted; the
  shipped configuration re-validated.** M72 closed by naming the uniform
  regret-matching fallback as the likely cause of AA's jam frequency
  growing with iterations. This milestone tested that, and the
  exploration-floor theory alongside it. Neither survived. No source
  changed — like M63, the deliverable is a closed-off search space and a
  corrected record.
  - **Refuted #1: the uniform fallback.** `current_strategy()` returns an
    exactly uniform distribution when every action's regret is <= 0,
    which hands the all-in a full 1/num_actions share; the theory was
    that without the CFR+ clamp more (infoset, hand) rows drift
    all-negative as iterations grow. Measured directly — the fraction of
    rows in that state is **~67-71% in every arm, and it DECREASES
    slightly with iterations** (clamped 70.9% -> 67.5%, unclamped 70.6%
    -> 67.2%). Nearly identical clamped vs not, and moving the wrong way.
    It cannot explain growth.
  - **A real characterization found while measuring it:** roughly **70%
    of all (infoset, hand) rows are never trained at all** on a 6-max
    169-class solve. MCCFR only visits sampled paths, so most rows never
    receive an update. This is the same phenomenon
    `InfoSetTable.trained_mask` exists to expose and that the M27-era
    diagnostic measured at 9-max; now quantified at 6-max, and it is why
    the clamped and unclamped arms look identical on this metric — in
    both, "all regrets <= 0" is dominated by "never visited", not by
    "went negative".
  - **Refuted #2: the exploration floor.** `EXPLORATION_EPSILON = 0.05`
    means opponents keep calling a 100bb shove ~1.25% of the time with
    ANY hand, forever. AA is never behind preflop, so a call from a
    random hand is a large win for the shover — and as CFR converges,
    opponents' genuine calling frequency falls toward zero and only the
    floor is left, which would keep jamming profitable and growing.
    Coherent, and it looked confirmed on first measurement: at 12,000
    iterations, dropping epsilon to 0.002 gave AA jam **0.024** and AKs
    **0.034**, both landing on the ~3% reference, against 0.381/0.483 at
    epsilon 0.05.
    **Then three seeds killed it:** 0.024 / 0.211 / **0.516**. The first
    reading was luck. Epsilon 0.01 had already been non-monotonic
    (worse than 0.05), which was the warning sign. This also firms up
    M70's own weaker "epsilon is not the culprit" result, which rested
    on a mixed reading in the clamped arm.
  - **What the data actually says, stated as a property rather than a
    cause:** at 6-max, AA's jam frequency is **stable and correct at
    3,000 iterations** (0.033 / 0.034 at epsilon 0.05; 0.039 / 0.017 at
    epsilon 0.002 — tight regardless of epsilon) and **unstable at
    12,000** (0.02 to 0.52 across seeds, at every epsilon tried). The
    instability is not caused by the clamp, not by the uniform fallback,
    and not by the exploration floor. M72's choice to ship 3,000 is
    re-validated by an independent route.
  - **Corrections applied in place:** M72's "the underlying cause is
    likely `current_strategy()` falling back to uniform" is marked
    refuted at that entry, and M70's epsilon note is strengthened from
    "mixed result" to "refuted with seeds".
  - **What is left for whoever picks this up:** the remaining untested
    candidates are the no-importance-sampling choice in
    `_mccfr_recurse`'s opponent action sampling (documented there as a
    bias "proportional to EXPLORATION_EPSILON, not tree depth", verified
    at N=3 and never re-verified at N=6), and the interaction between
    linear averaging and a drifting current strategy. Both are real; do
    NOT re-test the uniform fallback or epsilon.

- **M74 — The jam instability is policy OSCILLATION on a near-tied
  decision, not a bug in any one rule.** M73 ruled out the CFR+ clamp,
  the uniform regret-matching fallback, and the exploration floor. This
  milestone found what is actually happening by separating the POLICY
  from the AVERAGE, which nothing had done.
  - **The diagnostic that settled it.** Reporting
    `current_strategy()` alongside `average_strategy()` for AA at the
    root, unclamped:
    | iterations | seed | average jam | **current jam** |
    |---|---|---|---|
    | 3,000 | 1 / 2 | 0.034 / 0.033 | **0.000 / 0.000** |
    | 12,000 | 1 / 2 | 0.381 / 0.426 | **1.000 / 0.000** |
    The policy is **bang-bang** — exactly 0 or exactly 1, flipping
    between runs. AA's raise-versus-jam is near-tied under this tree and
    opponent model, so cumulative regret crosses zero repeatedly and
    regret matching swings the whole probability mass from one action to
    the other. The reported average is then just a function of which
    phase a run happens to stop in. At 3,000 the oscillation has not
    started (current jam is 0 on both seeds), which is exactly why the
    shipped budget is stable.
  - **Refuted #4: linear averaging as the cause.** It was the obvious
    suspect once oscillation was visible, since M69's weighting is
    recency-biased and would track the latest phase rather than smooth
    it. Measured at 12,000, three seeds each: uniform averaging gives
    0.257 / 0.397 / 0.445 (mean 0.366) against linear's 0.381 / 0.426 /
    0.554 (mean 0.454). So linear averaging **amplifies** the problem by
    roughly 0.09 — real, but it is not the cause, and uniform averaging
    is still badly wrong and badly spread. Linear averaging stays: it is
    a clear win on the fold axis (T7s folds 0.996 vs 0.950) and its
    amplification is small next to the oscillation itself.
  - **What this means, stated carefully.** The instability is not a
    defect in the clamp, the fallback, epsilon, or the averaging — it is
    that this decision is genuinely close under the model, and a
    deterministic regret-matching policy has no reason to settle on a
    mixture. Fixing it properly means damping the oscillation
    (averaging the POLICY rather than the visit-weighted strategy, or a
    simplex-projection / optimistic-regret variant), which is a real
    algorithmic change and a milestone of its own.
  - **Nothing shipped, deliberately.** M72 already set 6-max to 3,000,
    which is below the onset of the oscillation and measured stable
    (0.033 / 0.034). That remains the right operating point, now for a
    understood reason rather than an empirical one.
  - **Four hypotheses are now closed** (clamp, uniform fallback,
    epsilon, averaging). Anyone picking this up should start from the
    oscillation itself, not from another parameter.

- **M75 — Multiway turn and river were returning uniform, untrained
  "advice" 100% of the time. Now they are solved.** Started as "build
  multiway river"; found it already existed (M53 filled the last
  `/advise` cell — the "unscoped" note in CLAUDE.md referred to a
  dedicated `*_from_path` route, not the front door). Applying M72's
  lesson that HTTP 200 is not sanity turned up something much worse than
  a missing feature.
  - **The finding.** At production settings, a real 6-max 3-live-player
    line asking for turn advice returned **0 of 132 combos trained, every
    strategy exactly 1/num_actions**. The river the same. Hero holding a
    set of nines got `{call: 0.333, raise: 0.333, all_in: 0.333}` — a
    placeholder, not advice. Heads-up on the identical board and line
    returned a real strategy (`call 0.522 / all_in 0.478`,
    `trained=True`), which is what made it clearly multiway-specific.
  - **Confirmed NOT a regression from M66-M74.** The same probe on
    pre-M66 code (the M65 merge) gives 0 of 53. This has been true for as
    long as multiway turn/river has existed.
  - **The cause is structural, and was half-documented already.** MCCFR
    samples ONE next card per terminal per iteration, so
    `chance_data` only ever holds cards the solve happened to sample.
    `ensure_mccfr_chance_branch` (M44) correctly builds a missing
    (terminal, card) branch on demand — but left it **unsolved**, and its
    own docstring called that "a live endpoint's own necessary cost
    tradeoff, not a bug", describing a miss as something that "can
    easily" happen. The mechanism was right; the frequency estimate was
    not. A client asks about the card THEY were dealt, not one the solver
    liked, so the miss is essentially certain. Heads-up never had this
    because the exact solver's `build_chance_node` enumerates every card
    eagerly.
  - **The fix:** `ensure_mccfr_chance_branch` gained `train_iterations`,
    and runs `mccfr_solve` over the freshly-built branch's own subtree,
    seeded with the same per-position ranges the branch was built from,
    merging the result into `result.node_data` (safe — `node_data` is
    keyed by `id(node)` and the branch's nodes are fresh objects). One
    street's subtree is far cheaper than the parent solve.
  - **Measured at production settings** (marginal cost, preflop leg
    already warm):
    | train_iterations | turn | river | hero |
    |---|---|---|---|
    | 0 | 0/132 trained | 0/132 | untrained |
    | **100** | **44/132, 9.3s** | **44/132, 7.4s** | **TRAINED** |
    | 400 | 44/132, 17.1s | 27/132, 13.6s | trained |
    400 buys no extra coverage for double the cost, so
    `MULTIWAY_BRANCH_TRAIN_ITERATIONS = 100`. Coverage stops around
    44/132 because MCCFR samples paths — not every combo reaches every
    node — and `trained` reports that per combo rather than pretending.
  - **A test that asserted the limitation now asserts the fix.**
    `test_solve_turn_multiway_from_path_builds_and_returns_an_untrained_
    strategy_for_an_unsampled_but_legal_card` explicitly checked that
    every combo came back untrained. Renamed to `..._builds_and_trains_
    an_unsampled_but_legal_card` and rewritten to require that some combo
    is genuinely trained AND carries a non-uniform strategy — the second
    half matters, since "trained" alone would pass on a uniform answer.
  - **What this does NOT fix:** the honesty signals were working the
    whole time. `trained: False` was reported correctly on every one of
    those uniform strategies; nothing lied. What was missing was anyone
    reading it at production settings — which is exactly the gap M72's
    end-to-end check was introduced to close, now paying off a second
    time.
  - **Verification:** full backend suite green.

- **M95 — advice could name a bet the player cannot make; the stack
  bucket now rounds down.** R14 recorded this as F13 and closed it as
  inherent to the library's design. It was neither an edge case nor
  inherent.
  - **Not an edge case.** Swept across stacks and preflop lines: a
    **100bb stack in a limped pot** leaves 99bb behind and came back
    `all_in:100.00`. Any round starting stack that pays a blind is one
    bb short of its own bucket, so the failure case is the *default*
    stack on the simplest line there is. R14 saw it at 97.5bb and
    generalized toward "unusual depth" instead of "unraised pot".
  - **The fix R14 didn't consider.** It weighed keeping the bucketing
    (unaffordable advice) against relabelling the action to the real
    stack (honest number, strategy solved for another depth). The third
    option is to round the bucket **down**: `canonical <= real` becomes
    an invariant, so every size the tree derives — all-in and every
    raise — is affordable by construction, with nothing relabelled and
    no disagreement between the number and the strategy behind it.
  - **Measured price**, mean total-variation distance from a solve at the
    true depth, across every node of a real flop solve:
    | SPR | truth vs floor | truth vs nearest |
    |---|---|---|
    | 9.9 | 0.0083 | 0.0011 |
    | 2.3 | 0.0000 | 0.0000 |
    | 1.3 | 0.0000 | 0.0000 |
    | 0.6 | 0.0014 | 0.0009 |
    Floor is worse — by under 1% of probability mass at its worst,
    indistinguishable at three of four depths, well inside the noise the
    solve already carries. Bucket count is unchanged, so library hit rate
    is unaffected.
  - **The obvious repair reintroduced the bug.** A bare floor sends
    anything under one bucket to 0.0, which is not a game; clamping up to
    one bucket instead offered `all_in:5.00` to a player with **0.5bb**
    behind (8bb stack, raise-3bet-call). Caught by the new sweep test
    within a minute of writing it. Sub-bucket stacks are now used
    unbucketed — reuse lost only where a player barely has a decision,
    and the invariant then holds with no exception.
  - **Both tests sweep rather than sample**, because the old rounding
    passed every hand-written example including the ones already in
    `test_canonicalize.py`: 4,000 depths x 4 bucket sizes at the
    function, and 18 stack-by-line combinations at the `/advise`
    boundary. The second matters as much as the first — the response is
    where a user sees it.
  - **Verification:** 817 backend tests (up from 797), 152 frontend.

- **M96 — CLAUDE.md, the one file loaded into every session as current
  state, was the one file nothing verified.** M95's lesson was that a
  constraint written down as settled gets read past; the obvious next
  question is what else is written down as settled.
  - **Three of its four `api/config.py` claims had drifted:**
    `MAX_MULTIWAY_*_CLASSES_PER_POSITION` said 6 (is 8),
    `MAX_PATH_QUERY_CLASSES_PER_SIDE` said 6 (is 10),
    `MAX_TURN_PATH_QUERY_CLASSES_PER_SIDE` said 2 (is 4). Only
    `MULTIWAY_BRANCH_TRAIN_ITERATIONS = 100` was right. These are the
    numbers a reader reasons about cost and range width from. The two
    `_PER_SIDE` ones were true when M24/M26 wrote them, so they are now
    rewritten as "6 at the time" rather than corrected.
  - **The file contradicted itself twice.** It credited
    `_simulate_equity_shared_board` with **1.95x** while its own
    "Measuring performance" section, twenty lines below, records that
    number as **withdrawn in M70** — the file citing its own retraction
    and keeping the retracted figure. And the `trained`-flags entry
    describes M76's fix and then closes with the pre-M76 sentence
    asserting the opposite. Both are edit residue: a line updated, the
    sentence after it left alone.
  - **The fix is `tests/test_docs.py`, not a proofread.** Proofreading
    fixes today's copy; this file drifted under active maintenance the
    whole time. The test scans CLAUDE.md for `NAME = value` claims, keeps
    those naming a real `api/config.py` constant, and asserts each
    matches — parametrized, so a failure names the line and both values.
    Three deliberate limits: only mechanically checkable claims (prose
    would fail on rewording); unknown names ignored rather than failed (a
    test policing vocabulary gets deleted); and a guard on the guard,
    since a regex that stops matching makes every parametrized case pass
    vacuously with nobody noticing.
  - **Verified by mutation**, because a doc test never seen to fail is
    not known to work: changing a value yields `CLAUDE.md:128 says
    MULTIWAY_BRANCH_TRAIN_ITERATIONS = 400, but api/config.py has 100`.
    The withdrawn 1.95x gets its own named test — a retracted measurement
    reappearing is exactly what happens when someone summarises a file.
  - **Removed rather than corrected:** the "750 backend / 145 frontend"
    test counts (really 817/152) and "58 entries" for a 69-entry log.
    They cannot stay right and nothing depends on them; the command is
    the useful part.
  - **Verification:** 822 backend tests (up from 817), 152 frontend.

- **M97 — M74's prescribed fix for the 6-max jam oscillation, built in
  both forms it named, and both measured worse than doing nothing.**
  M74 diagnosed the instability as bang-bang policy oscillation and
  closed with "fixing it needs policy damping (averaging the policy, or
  an optimistic-regret variant) — an algorithmic change, not a
  parameter." This is that algorithmic change, and it is a negative
  result. Precedent: M18 (card abstraction wasn't the lever) and M73
  (two refuted hypotheses, no source change) — a closed-off search space
  is a deliverable.
  - **What was built.** `InfoSetTable.current_strategy` gained two
    optional modifications, both default 0.0 = off: `optimism` (the
    policy is matched against `regret_sum + optimism * last_regret` —
    predictive regret matching) and `smoothing` (the policy is blended
    with the one this node last played). Both thread through
    `_mccfr_recurse` and `mccfr_solve`.
  - **Measured** at 6-max, 3,000 iterations, three seeds, a fresh equity
    cache per run, against a heads-up AA-jam reference of ~0.031.
    **[Corrected by M98: this entry originally said "at the SHIPPED
    operating point" and that was wrong.** Every arm built
    `MultiwayEquityCache(hands=...)` without `samples`, taking the engine
    default of 200, where the API ships 50. The arms remain comparable —
    the mistake was identical across all three — but the operating-point
    claim was false, and it was false in the very milestone that quotes
    CLAUDE.md's rule about validating at the shipped point.]**
    | arm | AA jam | mean | spread | cost |
    |---|---|---|---|---|
    | plain | 0.036 / 0.073 / 0.058 | **0.056** | **0.037** | 262s |
    | optimism=1.0 | 0.562 / 0.963 / 0.359 | 0.628 | 0.604 | 258s |
    | smoothing=0.9 | 0.141 / 0.172 / 0.732 | 0.348 | 0.591 | 275s |
    Plain wins on level and stability at equal cost.
  - **Why each fails, which is the reusable part.** Prediction is a
    *full-information* technique; under external sampling the last
    instantaneous regret is dominated by the all-in — the noisiest action
    in the tree, the same one M71 identified — so it amplifies sampling
    noise instead of damping a cycle. Damping is a lag filter on regret
    matching's OUTPUT while the oscillation lives in `regret_sum`, its
    INPUT; a delayed oscillation is still an oscillation. Damping also
    has a hard ceiling: enough of it to outlast a cycle thousands of
    iterations long also stops the policy learning — at
    `smoothing=0.99`, AA jams 0.998 with a seed spread of **0.000**
    while T7s's fold *collapses* from ~0.94 to ~0.34.
  - **A refinement of M74's diagnosis.** At 12,000 iterations every arm
    sits at 0.40-0.61 mean; damping narrows the seed spread without
    moving the level. If that answer were purely a cycle sampled at a
    random phase, damping should pull the mean toward the cycle's
    average. It does not — which points at the question being asked (the
    equity model, the pool) rather than the policy dynamics. That is
    where the next attempt should look, and it is a better-directed
    hand-off than M74 left.
  - **Two-seed readings evaporated again.** Smoothing's first two seeds
    (0.141, 0.172) looked like a tight distribution; the third was
    0.732. Third time in this codebase (M73's exploration floor, M80's
    bias scare, this) — recorded in the docstring as a standing warning.
  - **A methodology error caught and correctly scoped.** The first two
    scripts shared one `MultiwayEquityCache` across arms, meaning to give
    none of them a colder start; instead cache warmth ended up aligned
    with arm order (a 3,000-iteration run went 283s -> 1.4s over a
    session). Results were re-run with a fresh cache per run — and came
    back **bit-identical**, because `MultiwayEquityCache` seeds each
    entry from `(seed, opponent hands)` via `_stable_seed` rather than a
    shared advancing RNG, a property its own docstring states. So the
    confound was real but affected **timings only**: the interim
    readings that predictive was faster and smoothing 2x slower were
    artifacts, and under equal conditions all three arms are within 7%.
  - **The knobs stay, the memory does not.** Both parameters remain
    (like `discount` after DCFR measured worse) so the result is
    reproducible. But `last_regret`/`last_strategy` are stored ONLY when
    the flag that reads them is on: they are two more (num_hands,
    num_actions) arrays beside `regret_sum`/`strategy_sum`, which exactly
    **doubles** `node_data` — 8,500-9,300 tables on a 6-max solve, the
    largest structure any solve produces and the one M93 had just
    finished bounding. A default-off feature pays nothing.
  - **Verification:** 831 backend tests (up from 822), 152 frontend.

- **M98 — the multiway sizing defect is structural, not a budget
  problem; and the product now says so.** M97 ended by pointing at "the
  question being asked" rather than the policy dynamics. This is that
  investigation. It found a root cause, corrected M97's own operating
  point, and shipped the honesty signal the finding implies.
  - **Root cause.** Every showdown terminal is priced `equity * pot -
    invested` (`cfr._mccfr_terminal_value`). An all-in is therefore
    priced CORRECTLY — it really does end at showdown — while every
    smaller bet is scored as if the hand ended immediately, discarding
    the postflop game that is most of a raise's value. AA raising 2.5bb
    into a caller is worth `0.85 * 5.5 - 2.5 = +2.2bb` to this model;
    jamming into a caller is worth `+70bb`. The error grows with
    opponent count, because more opponents means more chance the
    accurately-priced all-in gets called at all.
  - **Not a budget problem, measured.** At 12,000 iterations and 400
    equity samples — the most converged, least noisy configuration
    tested — AA jams **0.649** and KK **0.709**. More iterations and
    more samples converge ONTO the jam rather than away from it. The
    long-standing note that the sizing split was "not converged at this
    budget" implied more budget would fix it; it will not.
  - **Why heads-up is unaffected: NOT established.** [Corrected in M99.]
    This entry originally claimed heads-up "escapes by cancellation" —
    that a jam there just wins the 1.5bb blinds, worth less than a
    called raise even underpriced. That was a hypothesis stated as a
    finding, and its arithmetic does not survive scrutiny: a jam's value
    depends on villain's calling frequency against the WHOLE shoving
    range, not on AA in isolation, and the equilibrium jam frequency is
    a property of the range's incentives rather than one hand's. What is
    measured is the pricing rule and the 6-max convergence onto jamming.
    Why N=2 escapes remains open.
  - **Equity noise explains the INSTABILITY, not the level.** Measured
    directly: a 50-sample multiway equity estimate has error sd 0.091 —
    **+/-55bb of EV in a six-way 100bb pot**, worst observed 141bb, with
    one opponent tuple's estimate ranging 0.216-0.583 against a truth of
    0.348. The cache freezes each estimate per key, so CFR optimizes
    against its own noise instead of averaging it away. That is the seed
    dependence; it is not the jam-heavy level.
  - **The warning was already written down.** `equity.py`'s
    `MULTIWAY_DEFAULT_SAMPLES = 200` comment says, since M8, that at 50
    samples equity noise gets "amplified by the all-in pot size into a
    large enough value error to visibly distort MCCFR's learned
    strategy". `api/config.py` overrode it to 50 on a comparison that
    varied sample count and iteration count TOGETHER and reported only
    fold rates. Every tuning decision on this constant was measured on
    the fold axis; the sizing axis was written off rather than measured.
  - **A correction to M97.** M97 claimed its arms were measured "at the
    SHIPPED operating point". They were not: every script built
    `MultiwayEquityCache(hands=...)` without `samples`, taking the
    engine default of 200 where the API ships 50. The arm-vs-arm
    conclusion stands (all three made the identical mistake) but the
    label was false — in the very milestone that quotes CLAUDE.md's rule
    about validating at the shipped point. Corrected in `cfr.py`,
    `milestones.md`, the diagnostic and CLAUDE.md.
  - **What shipped: `sizing_confidence`.** A multiway preflop solve
    answers two questions and is only good at one, and one confidence
    number could not say that — so a 6-max player asking "raise or
    shove?" got the same `solver_confidence: "high"` as one asking "play
    or fold?". `sizing_confidence` / `sizing_confidence_reason` are
    separate from `solver_confidence` deliberately: marking the whole
    response low would understate the fold-vs-play call, which is the
    converged part and what most players are asking. Scoped to preflop
    and rendered in `AdviseSolver`, with tests asserting it fires at
    3/6/9-max, does NOT fire heads-up or postflop, and coexists with
    9-max's existing warning without either hiding the other.
  - **Deliberately not attempted:** postflop continuation value at
    preflop terminals, which is what an actual fix requires. That is an
    architectural change to how terminals are priced, and it deserves
    its own milestone rather than being bolted onto a diagnosis.
  - **Verification:** 835 backend tests (up from 831), 154 frontend (up
    from 152), tsc and lint clean.

- **M99 — the shipped sample count is right, its justification was not;
  plus four corrections to what M98 claimed versus what it showed.**
  M98 found the multiway sizing defect and implicated
  `MULTIWAY_PREFLOP_SAMPLES = 50` in it. This re-measured that constant
  properly, expecting to change it, and found it should stay.
  - **The re-measurement.** The constant's original evidence compared
    `200 samples @ 300 iters` (~170s) against `50 @ 3,000` (325s) — two
    variables at once, two different costs, one diluted metric. M99 held
    wall clock roughly fixed, 9 seeds per arm, reading T7s's
    under-the-gun fold (a MARGINAL hand; trash like J4o/95o folds ~0.999
    in every arm and cannot discriminate):
    | arm | T7s fold +/- SE | worst | below 0.8 | AA jam | cost |
    |---|---|---|---|---|---|
    | **50 x 3,000** | **0.866 +/- 0.051** | 0.486 | **2/9** | 0.116 | **98s** |
    | 200 x 750 | 0.485 +/- 0.099 | 0.061 | 8/9 | 0.832 | 144s |
    | 400 x 375 | 0.419 +/- 0.111 | **0.000** | 7/9 | 0.991 | 149s |
    Iterations dominate, and not marginally — starve them and one seed
    in nine folds T7s **0%** of the time under the gun while jamming AA
    99%. The shipped arm wins on every measure and is also cheapest.
    Note the arms are NOT equal-cost despite holding `samples x
    iterations` constant: per-iteration tree/NumPy work does not scale
    with samples, so the 200- and 400-sample arms cost ~47% more.
  - **A separate 9-seed sweep at fixed iterations** (3,000) showed what
    samples DO buy: stability. AA-jam SE falls 0.066 -> 0.098 -> 0.021
    and its range narrows 0.013-0.635 -> 0.015-0.204 going 50 -> 200 ->
    400, while T7s's fold rises 0.866 -> 0.924 -> 0.942. But at fixed
    COST that stability is not worth the iterations it costs.
  - **What none of it fixes:** at the shipped setting 2 seeds in 9 still
    fold T7s below 0.80, one at 0.486. That is what M98's +/-55bb frozen
    equity error buys, and no retuning removes it.
  - **A metric that hid the defect it was built to find.** The
    equal-cost script averaged three hands that should fold. J4o and 95o
    fold ~0.999 everywhere, so the mean reported **0.955, worst 0.829,
    0/9 below 0.80** for the very same nine solves where T7s alone shows
    **0.866, worst 0.486, 2/9**. Same runs, two metrics, opposite
    verdicts. Diluting a metric with cases that cannot discriminate does
    not merely lose power — it conceals the defect.
  - **Four corrections to M98, separating measured from inferred:**
    1. M98's claim that heads-up "escapes by cancellation" was a
       hypothesis written as a finding, and its arithmetic does not hold
       (a jam's value depends on villain's calling frequency against the
       WHOLE shoving range, not one hand's). Why N=2 is unaffected is
       **open**. Corrected in four files.
    2. M98 scoped `sizing_confidence` to preflop on the grounds that
       postflop "carries its own trained/range_confidence signals" —
       which describes the RANGE and says nothing about terminal
       pricing. Flagged as an open question in `api/config.py`.
    3. `test_heads_up_preflop_does_not_carry_the_sizing_caveat` argued
       heads-up is "solved exactly" so the caveat would be decoration.
       Exact SOLVING does not repair a PRICING defect; heads-up preflop
       has the same three unmodelled streets. The test now pins current
       behaviour and says the soundness is unmeasured — and unmeasurable
       in-repo, since no deeper preflop tree exists to compare against.
    4. A stale comment in `_advise` still described M84's routing through
       `solve_flop_turn` with a shared turn cache; M88 replaced that with
       `solve_flop` and `_flop_node_cache` eleven milestones earlier.
  - **ANSWERED: the pricing flaw DOES reach the flop.** `solve_flop` is
    flop-only (two unmodelled streets) and serves heads-up flop advice,
    so the prediction was concrete. Measured on identical board, ranges,
    pot, stack, sizes and cap — the ONLY variable is how much future
    betting the tree can see:
    | tree | all-in | check | mixedness | nodes |
    |---|---|---|---|---|
    | flop only | **0.5652** | 0.4348 | 0.687 | 4 |
    | + real turn | **0.5099** | 0.4901 | 0.601 | 200 |
    | + turn and river | **0.4635** | 0.5365 | 0.592 | 9,608 |
    Each street of future betting the tree gains moves ~5 percentage
    points off the all-in — **10.2pp monotone** from flop-only to fully
    chained, in the predicted direction. All three use the exact
    two-player solver and are deterministic, so this is a genuine
    difference, not sampling noise. The monotonicity is the part that
    matters: it is what the mechanism predicts and what a coincidence
    would not produce. Magnitude is still modest next to preflop's, which
    fits the severity ordering (preflop three unmodelled streets, flop
    two, turn one, river zero).
  - **The first fixture had to be thrown away, and that is the lesson.**
    It used the 2-class demo ranges at SPR 9.5, where the solution is
    0.9999 pure and adding a turn moved the root strategy by 2.9e-04 — a
    degenerate fixture cannot detect an effect whether or not it exists,
    and reading its null as a finding would have wrongly closed the
    question. The replacement widened both ranges (value/marginal/draw/air)
    and dropped SPR to 1.5 so the all-in genuinely competes, and reports a
    **mixedness number** so a real null can be told apart from another dead
    fixture. Note what the low SPR costs: a 2.5x-pot raise exceeds the
    stack there and collapses into the all-in, so this measures weight
    moving between all-in and CHECK, not all-in and raise.

- **M100 — the architectural fix M98 pointed at, tested cheaply first,
  and NOT validated.** M98 found that `equity * pot - invested` prices
  an all-in correctly and every smaller bet as if the hand ended there,
  and named postflop continuation value at preflop terminals as the real
  fix. That is expensive architecture (chaining a flop off every preflop
  terminal), so this milestone asked the cheap question first: is the
  diagnosis SUFFICIENT — does restoring *any* continuation value remove
  the jam bias — or merely consistent with it?
  - **The probe.** `_mccfr_terminal_value` gained `continuation` /
    `stack_bb` (default 0.0, off, byte-identical to M99). Where chips
    remain behind, a hand's payoff gains
    `continuation * (equity - 1/n_live) * chips_behind` — a crude stand-in
    for the postflop game the tree cannot see. Two properties make it a
    fair test rather than a thumb on the scale, both pinned by tests: it
    applies ONLY where chips remain, so an all-in terminal is untouched
    (that asymmetry IS the defect), and it is exactly zero-sum at equal
    stacks, so it cannot inject chips and fake an improvement.
  - **The result: an incoherent dose-response.** AA's all-in frequency,
    three seeds per cell:
    | budget | c=0 | c=0.25 | c=0.5 | c=1.0 |
    |---|---|---|---|---|
    | 12,000 | 0.615 | 0.208 | 0.417 | 0.374 |
    | 3,000 | 0.061 | 0.112 | 0.287 | 0.010 |
    Non-monotone at both budgets. A term capturing a real mechanism
    should move the number one way as it is turned up; this goes down,
    up, down.
  - **The trap, and the lesson.** `c=1.0 @ 3,000` gives **0.010 +/-
    0.005** — an order of magnitude tighter than any other arm, and very
    easy to report as the fix. It is not one. A large bonus for keeping
    chips behind makes the all-in *dominated*, so the policy goes purely
    "never jam" and returns a stable near-zero — BELOW the ~0.031
    reference. That is hitting the target by making the action
    unattractive, not by modelling what follows it. **This knob can
    produce any number, so matching the reference does not validate it.**
  - **What it does NOT show.** It does not refute M98's diagnosis — the
    pricing asymmetry is read straight from the code and M99 confirmed
    it postflop with a monotone 10.2pp effect. It shows that a *linear
    edge-times-stack stand-in* is not a valid substitute for solved
    continuation values, so the architectural work cannot be justified
    (or costed) on this evidence and still needs its own milestone.
  - **The paired 9-seed test settles it: a NULL.** The sweep used 4
    coefficients x 3 seeds; within-arm spread (0.065 to 0.487 at c=0.25)
    matched the between-arm gaps, so nothing separated. Replaced with a
    **paired** design — both arms share a seed, so the per-seed
    difference cancels seed variance entirely rather than averaging it
    away — plus a sign test over the nine pairs. Result, c=0 vs c=0.25 at
    12,000 iterations:
    | | AA jam +/- SE | range |
    |---|---|---|
    | c=0 | 0.494 +/- 0.068 | 0.258-0.856 |
    | c=0.25 | 0.434 +/- 0.090 | 0.065-0.820 |
    **Paired delta -0.060 +/- 0.137, fell in 5/9.** Five of nine is a coin
    flip. The continuation term does not reduce the jam.
  - **The interim of a paired design is no safer than any other
    interim.** At 4 pairs this read 4/4 falling, mean -0.31, and looked
    like a real effect; seeds 5-9 erased it. Pairing removes seed
    VARIANCE, not the need for the full sample. Three separate times this
    milestone a partial result pointed the wrong way — `0.0431` read as
    an arm at c=1.0, `c=0.25 @ 12k` read as a fix in the 3-seed sweep,
    and 4/4 read as a trend here.
  - **Also learned:** at 12,000 iterations the jam is seed-determined in
    EVERY arm including the uncorrected one (individual seeds span
    ~0.04-0.86). 12,000 is where the defect is largest and also where
    measurement is least reliable — the shipped 3,000 is four times
    tighter (SE 0.032 vs 0.120).
  - **`continuation` stays, default 0.0**, like `optimism`/`smoothing`
    after M97: a refuted approach that stays reproducible is worth more
    than one that is only remembered. Unlike those, it costs no memory —
    it is arithmetic, not a stored array.
  - **Also shipped, from M101's audit:** `tests/test_comment_drift.py`,
    which flags a comment claiming its code "goes through / calls / uses"
    a function the enclosing function never calls — the exact shape of
    the stale `_advise` comment M99 found by accident. Contrast wording
    ("unlike X", "used to go through X") is exempt, because those are the
    most valuable comments here. Matched on comment BLOCKS not lines,
    since a disclaimer usually sits a line above the claim it disclaims —
    the line-based first version false-positived on M99's own correction
    note. A guard test reconstructs M99's defect and requires a hit, so
    the checker cannot rot into one that never fires. The audit's first
    attempt (regex for `M\d+ .* now|currently|as of`) flagged 8 lines,
    all accurate, and missed the real one — a detector that fires on
    comments which merely SOUND like claims trains you to skim it.
  - **And:** the only two constants in `api/config.py` without a measured
    justification (`DEFAULT/MAX_MULTIWAY_TURN_PATH_QUERY_FLOP_ITERATIONS`)
    now say so explicitly rather than being silently unexplained. They
    inherited their values by analogy with the flop sibling; that
    reasoning is sound and is still not a measurement, and the comment
    says which is which.
  - **Verification:** 842 backend tests (up from 835), 154 frontend.

- **M101 — third full audit, and its findings acted on.** A fresh pass
  over the whole product: static checks, a live-play simulation against
  the real API, and cold-vs-warm benchmarks. Full write-up in
  `docs/audit-2026-08-23.md`; four findings, all resolved here.
  - **F19 (high): a malformed request cost 76 seconds to reject.** A
    cold 6-max request whose preflop path leaves four players still to
    act took **76.2s to return a 422** (0.002s warm). Two causes, one
    shape of reasoning: the live-player count fetched the SOLVE in order
    to walk a tree, and the path-shape check sat behind the solve rather
    than in front of it. The first carried a comment justifying itself —
    *"the preflop solve is already cached, so this is a tree walk, not a
    second solve"* — which is true for the second caller and wrong for
    the first, who is the only one waiting. Both now build a throwaway
    tree (`game_tree` builds children lazily, so walking one path
    materialises only that path): **76.2s -> 0.1s, 760x.** Pinned by a
    test that counts SOLVES rather than seconds, because a wall-clock
    bound would flake on a machine that drifts ~1.7x between sessions.
  - **F20 (medium): the affordability guarantee was only checkable at
    opening decisions.** M95 promised no advice names a bet the player
    cannot make and swept every street to prove it — but only each
    street's *opening* decision, which is the one place
    `effective_stack_bb` means "money behind". Elsewhere it means
    something else: preflop it is the stack net of blinds while preflop
    sizes are TOTAL commitment; one decision into a street it is the
    shortest remaining stack after someone bets. So a real flop node
    reports `effective_stack_bb: 85.0` beside `all_in:97.50` — both
    correct, not comparable, and a caller cannot answer "can I afford
    this?" from the response. Fixed with a separate `max_affordable_bb`
    (the largest total commitment the acting player can make on this
    street), swept at mid-street nodes, plus a test asserting the two
    fields genuinely DIFFER at the node that motivated it so the new one
    cannot decay into a copy of the old.
  - **F21: a stale-comment detector, and why the obvious one fails.**
    Shipped as `tests/test_comment_drift.py`. The obvious version — a
    regex for `M\d+ .* (now|currently|as of)` — flagged 8 lines, all
    accurate, and missed M99's real defect entirely, which contains none
    of those words. Matching comment BLOCKS not lines (a disclaimer sits
    a line above the claim it disclaims), exempting contrast wording, and
    guarded by a test that reconstructs the original defect and requires
    a hit.
  - **F22: two constants with no measured justification** now say so
    explicitly instead of leaving the gap silent.
  - **An audit check that was wrong, recorded as such.** The harness
    flagged `hero_in_range: false` as "no advice"; it is a designed
    signal (hero's combo was force-included at the range minimum after
    failing to survive the cap). And it flagged every preflop all-in as
    unaffordable — which is *how* F20 was found, but on a false premise
    that the fields share a baseline. **A checker that reports a real
    defect for the wrong reason is lucky, not right.**
  - **Confirmed healthy:** layering clean (`config <- caches <- solving
    <- main`, zero violations), no TODO markers, no untested module, no
    uncalled public solver entry point, all four impossible-request
    probes refused 422 with specific reasons, honesty signals firing on
    the right cells, every warm response under 25ms.
  - **Verification:** 849 backend tests (up from 842), 154 frontend.

- **M102 — audit round 2: input fuzzing, and a typo that became a
  confident answer to a different question.** 29 malformed,
  contradictory and boundary inputs at `/advise`, classified by whether
  each failed cleanly. Write-up in `docs/audit-2026-08-23.md`.
  - **F23 (high): unknown request fields were silently discarded.**
    Pydantic's default. Measured on the real endpoint:
    `{"hero_card": "AsAh"}` returned **200 with `hero: null`** — the hand
    the user asked about, silently dropped — and `{"player": 6}` returned
    **200 with `players: 2`**, answering a 6-max question with heads-up
    advice. Both look exactly like correct responses. Every request model
    now sets `extra="forbid"`, so an unknown field is a 422 naming it.
    Safe for the frontend, which builds its body from a typed
    `AdviseRequest`.
  - **F24: CLAUDE.md described an input that does not exist.** Its "What
    it does today" line said the product takes "your position".
    `AdviseRequest` has no such field — the acting seat is derived from
    the action path. The wording was wrong long enough that **every probe
    written this session sent `position`, and so did six tests**, all of
    it silently inert. Turning on `extra="forbid"` surfaced 22 suite
    failures at once, every one a test sending a field that never
    existed — the finding proving itself. (The older `/solve` and
    `/solve_flop` endpoints *do* take and validate a `position` query
    parameter, which is why the confusion was plausible.)
  - **Deliberately not fixed:** `stack_bb: 1e9` is accepted and returns
    `all_in:1000000000.00`. Measured as non-pathological — same solve
    time as 100bb, structurally correct output. It is absurd, not
    impossible; there is no principled cap; an invented one could refuse
    a legitimate deep-stack query. Recording the reasoning beats adding a
    magic number.
  - **The fuzzer was confidently wrong twice, in opposite directions.**
    First run: three 422s scored "unhelpful" because the classifier's
    word list lacked "rank" and "suit" — the messages were fine. Second
    run: a perfect **31/31 clean**, also false, because the fuzzer's own
    base body still contained `position` and `extra="forbid"` had just
    made it invalid, so every case failed for that reason instead of the
    one under test. M101 recorded that a checker reporting a real defect
    for the wrong reason is lucky, not right; **this is the mirror image,
    and worse — nobody investigates a pass.**
  - **Verification:** 855 backend tests (up from 849), 154 frontend.

- **M103 — audit round 3: the five UI tabs nobody had ever driven.** M94
  drove the Advisor in a browser; the other five tabs had never been
  opened by anything but a human, and every frontend test stubs `fetch`,
  so nothing had confirmed they reach the real API.
  - **All five work.** Preflop Ranges (`GET /solve/100`, full grid),
    Equity Calculator (**AA vs KK 82.0%/18.0%**, matching the known
    ~82.4%), Flop Solver (0.18s), Cached Flop Solver, Multiway Flop
    Solver (3-max, 0.27s). No console errors.
  - **Canonical reuse confirmed in the UI for the first time.** `Jh7d2c`
    solved live in **327.35ms**; suit-shuffled to `Js7h2d` it reported
    **"Cache hit — 0.30ms"** against the same canonical `2c7dJh`. M20/
    M21's central claim, demonstrated through the real product rather
    than a fixture.
  - **The honesty signals reach the demo tools.** Uniform 33/33/33
    combos carry a "low data" label in both flop solvers — M82's lesson
    holding where nobody had checked.
  - **F25: nothing verified the app was served at all.** The sweep found
    no defect in the tabs and one underneath them: the whole suite talks
    to `/advise` and `/solve` directly and **never requests the page a
    user opens**. If `frontend/dist` vanished, or the catch-all static
    mount stopped being registered last and began swallowing API routes,
    every tab would break at once with a green suite. Two tests now
    cover it — the shell is served with a bundle reference, and
    `/solve/100` still returns `application/json` rather than HTML. The
    mount is registered last *on purpose*, which is exactly the kind of
    ordering a later edit reshuffles silently.
  - **Verification:** 857 backend tests (up from 855), 154 frontend.

- **M104 — audit round 4: concurrency and memory, the two things a unit
  suite cannot see.** It never issues two requests at once and it clears
  caches between cases. Both properties had milestones behind them (M92,
  M93) and neither had been rechecked. Write-up in
  `docs/audit-2026-08-23.md`.
  - **F26 (high): the thundering herd M92 missed.** Counting real
    `solve_preflop` calls through the endpoint: **8 concurrent cold
    requests ran 8 solves** (24.21s) where one was needed. After:
    **1 solve, 10.44s.** `_get_or_solve_preflop_raw` still used
    check-then-compute — every thread checked, found the cache empty,
    and solved. M92 replaced that pattern everywhere it found it and
    missed the heads-up preflop solve, which every heads-up POSTFLOP
    request depends on first. It survived because the suite never issues
    two requests at once, and because duplicated work is invisible from a
    single caller: every response is correct, just paid for N times. The
    test counts SOLVES, not seconds, and was verified by mutation —
    restoring the old code makes it report "6 preflop solves ran for 6
    concurrent requests".
  - **F27: two unbounded caches keyed on request input.** M93 bounded
    thirteen and left two alone, justified as "at most a few dozen
    entries ever exist ... the startup pre-warm fills them on purpose".
    True of the entries the pre-warm creates; false of the key, which is
    `(round(stack_bb), ...)` on both. A client walking stack depths mints
    an entry per integer depth forever — **0.152 MB of heap each**.
    Bounded at 64 (multiway, 75-140s to rebuild) and 128 (preflop_raw);
    verified that 500 distinct depths collapse to 64 and 128.
  - **The composition is the real lesson.** M102 measured `stack_bb: 1e9`
    as absurd-but-valid and deliberately left it uncapped — correct in
    isolation, and still correct. Feeding an unbounded cache keyed on
    that value, it becomes a memory-exhaustion path. **Neither finding is
    a defect alone.** The invariant test therefore asserts over every
    module-level cache rather than naming the two that were wrong, since
    the next one added will be wrong the same way; `multiway_equity` is
    exempted explicitly with its reason (keyed by a config constant, so
    its key space is one entry) so that an allowlist entry stays a
    decision rather than a shrug.
  - **Healthy:** no errors under 8-way concurrency (the defect was cost,
    never correctness), and every previously-bounded cache stayed within
    its ceiling.
  - **Verification:** 860 backend tests (up from 857), 154 frontend.

- **M105 — audit round 5: the hand evaluator, verified exhaustively. No
  defect.** Every equity number is a count of how often one hand outranks
  another and every strategy derives from those counts, so a ranking bug
  makes spots quietly wrong rather than failing loudly. The 22 existing
  tests were example-based. Write-up in `docs/audit-2026-08-23.md`.
  - **All four checks clean:** a category census over **all 2,598,960**
    five-card hands matching the nine published frequencies exactly; the
    vectorized and scalar evaluators agreeing on ordering AND ties across
    all 2,598,960; `best_hand_rank` matching brute force over every
    C(7,5) subset on 40,000 seven-card hands; and the vectorized 7-card
    path agreeing with brute force. **The foundation is sound**, which
    also means M98's sizing defect and M98's ±55bb equity noise cannot be
    blamed on hand evaluation.
  - **The census is now permanent, at 2.7s for 2.6M hands**, because it
    runs the vectorized path — the one every equity computation uses. It
    asserts against PUBLISHED frequencies, not against anything this
    codebase produced; an evaluator cannot be validated by its own output.
  - **Verified by mutation, both ways.** Removing the wheel-straight rule
    makes the census fail. A tiebreak-only corruption does not (categories
    are unchanged) and is caught by the ordering tests instead — so each
    guard was checked against a mutation the other cannot see.
  - **Two corrections to my own work.** The first mutation attempt wrote
    the wheel as `{14, 2, 3, 4, 5}`; this codebase represents an ace as
    **12**, with the wheel handled by adding `-1`. The mutation was inert,
    tests passed, and I briefly read that as the census being blind — **a
    mutation check that does not mutate proves nothing**, the same shape
    as M102's fuzzer scoring a meaningless 31/31. And the stratified
    sampling I added in response was aimed at a gap that did not exist;
    it is kept because forcing every category is better sampling design
    and costs 0.13s, but it **overlaps four pre-existing tests** rather
    than covering anything new. The exhaustive census is the real
    addition.
  - **Verification:** 863 backend tests (up from 860), 154 frontend.

- **M106 — audit round 6: equity verified against enumeration, and an
  audit whose reference data was wrong three times.** M105 verified hand
  evaluation; equity is the layer above it, and the one M98 measured
  carrying ±55bb of noise multiway. Write-up in
  `docs/audit-2026-08-23.md`.
  - **No defect in equity.** Checked against exact enumeration of all
    C(48,5) = 1,712,304 boards, Monte Carlo tracks truth to within
    **0.18pp** on every matchup tested. Two layers now verified clean.
  - **The audit's own reference table had three errors, the worst by
    21pp.** `77 vs 65s` was entered as 0.606 and is truly **0.8184** —
    0.606 is the figure for a pair facing OVERCARDS, and 65s is entirely
    below a seven. It looked exactly like a serious engine bug.
    `AKs vs QJs` (0.634 vs true 0.6595) and `A5s vs KQo` (0.588 vs true
    0.5995) were wrong the same way.
  - **The symmetry "violation" was my threshold.** `equity(a,b) +
    equity(b,a) = 0.9972` against a `> 1e-9` assertion — but the two
    directions deal their own cards and sample independently, so the
    deviation is inside one standard error. An exact assertion there
    measures the RNG, not the property.
  - **The lesson is M105's, one layer up.** Category frequencies are
    exact combinatorial facts and safe to quote from memory. Matchup
    equities are suit-configuration dependent and are not. **Ground truth
    a repository can regenerate beats a number recalled from outside
    it** — so the permanent tests freeze enumeration-derived values and
    keep the enumerator beside them, plus a guard that re-derives one, in
    case `deal_two_hands` ever assigns different concrete cards and the
    frozen numbers silently stop describing what it returns.
  - **Each guard checked by mutation.** Awarding ties to one player
    instead of splitting is caught by the SYMMETRY test and not by the
    absolute-value comparison (ties are too rare in these matchups to
    move equity past the 1pp tolerance); an equity-shifting error is
    caught by the absolute comparison. Complementary, and demonstrated
    rather than assumed.
  - Cost tuned 105s -> 54s; seeds are fixed, so the tests are
    deterministic rather than merely unlikely to flake.
  - **Verification:** 869 backend tests (up from 863), 154 frontend.

- **M107 — audit round 7: /advise vs the endpoint it replaced, and
  determinism. No defect.** `api/solving.py` claims `/advise` is "a
  unified FRONT DOOR, not a second implementation to keep in sync" and
  "deliberately delegates rather than reimplements". Testable, the
  deprecated routes are still live and reachable, and nothing tested it.
  Write-up in `docs/audit-2026-08-23.md`.
  - **Cross-endpoint agreement is EXACT.** `/solve_flop_from_path` and
    `/advise` on the same spot, caches cleared between: identical
    position, pot, effective stack, combo set, and a **worst strategy
    delta of 0.0** across three lines. Asserted as exact equality, not
    approximate — delegation means the same solve, so any difference at
    all means a second implementation has appeared.
  - **Determinism is exact.** The same request twice with caches cleared
    in between (so the second genuinely re-solves) is bit-identical at
    heads-up preflop, flop, mid-street flop, and 3-max preflop. Without
    the clearing the test would pass trivially by reading one solve
    twice; what it rules out is accumulated state making advice depend on
    what the server did beforehand.
  - **Both guards mutation-checked.** Giving `/advise`'s flop cell a
    different iteration budget from its sibling fails all three
    consistency cases. Making the sampled solver's seed depend on call
    order fails the 3-MAX determinism case and not the heads-up ones,
    which use the exact solver — which is exactly why 3-max is in the
    parametrisation.
  - **Diminishing returns, stated plainly.** Rounds 5-7 found no
    defects; rounds 1-4 found six, every one in a seam (cost, contract,
    composition, reachability) rather than in the solver. The engine
    layers are now sound and guarded. The remaining known-wrong thing is
    the one testing cannot fix: M98's structural sizing defect, which
    needs solved postflop continuation values. Recorded so the next round
    is chosen deliberately — likelier value lies in seams never probed
    (`derive_ranges_from_path`'s reach multiplication, behaviour when a
    solve raises mid-request) than in re-verifying layers already shown
    correct.
  - **Verification:** 876 backend tests (up from 869), 154 frontend.

- **M108 — audit round 8: the two seams M107 named. No defect in
  either.** M107 recorded that further value lay in seams never probed
  rather than re-verifying verified layers, and named two. This is those
  two. Write-up in `docs/audit-2026-08-23.md`.
  - **A failing solve behaves correctly.** The failure mode that would
    matter is not an ugly error but a **200 with fabricated advice** —
    exactly what this codebase's honesty machinery exists to prevent. It
    does not happen: the exception propagates as a server error, the
    server recovers, and the post-failure answer is **bit-identical** to
    a clean one computed beforehand, so no partial entry survives.
    Checked separately from recovery, because "the next request returns
    200" is a weaker claim than "the next request is right". Deliberately
    NOT converted to a friendly 422 — dressing an unexpected solver bug
    as a validation failure would hide it.
  - **Reach multiplication is doing real work.** A raiser's derived range
    weights KK **640x** above T2o; a three-bettor's is more concentrated
    still; a limper's tilts slightly toward trash, which is correct since
    strong hands raise. No negative weights; no two paths derive the same
    range. The zeros are consequences of the strategy, not gaps — BB
    holds no AA in a limped pot because AA always raises.
  - **Guards are comparative, not snapshots.** Three-bettor stronger than
    raiser, raiser stronger than limper — exact weights depend on the
    preflop budget and would make the tests brittle, while the ordering
    is what collapses if the conditioning stops. Verified by mutation:
    replacing `reach[actor][hand] * freqs[hand]` with `reach[actor][hand]`
    fails both range guards.
  - **A third inert injection, caught before it became a conclusion.**
    The failure probe first patched `api.solving.solve_flop` and got a
    **200**, which reads as "a failing solve returns confident advice" —
    a serious finding. It was not: the flop path solves through
    `poker_solver.library`, so nothing was intercepted. Third time this
    session an instrument produced a confident result while doing nothing
    (M105's ace value, M102's fuzzer base body, this), so the shipped
    test now asserts the injected failure actually fired and says so in
    its message.
  - **Verification:** 881 backend tests (up from 876), 154 frontend.

- **M109 — audit round 9: cache key completeness. No defect.** M76 found
  `hero_cards` changed the solved pool but was missing from the
  path-query cache keys, so on a shared server the first asker fixed the
  answer and everyone after got advice for someone else's hand. Nothing
  had checked whether another field did the same, and it is structurally
  invisible to the suite — whose fixture clears caches, making every test
  the first caller. Write-up in `docs/audit-2026-08-23.md`.
  - **All six fields reach the key** (`stack_bb`, `board`, `hero_cards`,
    `preflop_action_path`, `flop_action_path`, `players`): each changes
    the answer on a cold cache, and each gives the same answer warm.
  - **The method separates "missing from the key" from "has no
    effect"**, which a naive comparison cannot: run the variant warm
    (after a baseline populated the cache) and cold (alone). Identical
    means the key carries it; differing means the warm request was served
    a previous caller's solve.
  - **Verified against the original bug.** Making `_hero_cache_component`
    return `None` — M76's exact defect — makes the probe report `KEY
    INCOMPLETE` on `hero_cards` and no other field, and the shipped test
    fails on that case alone. After three inert instruments this session,
    a clean result is only worth reporting once the probe has been shown
    to detect what it exists for.
  - The parametrisation carries its own guard: a field with no effect
    would make warm and cold agree for the wrong reason, so a separate
    test asserts that changing the board really does change the answer.
  - **Verification:** 887 backend tests (up from 881), 154 frontend.

- **M110 — maximal benchmarks, and a live-play GTO comparison that found
  a new defect.** Full report in `docs/benchmarks-2026-08-24.md`. Every
  timing carries reference units against a workload measured in the same
  run (0.277s), since this machine drifts ~1.7x between sessions.
  - **Warm is flat and fast:** every `/advise` cell serves in 1.7-9.7ms
    whatever the table size — 9-max preflop (1.86ms) beats heads-up flop
    (4.75ms), because warm cost is response shaping, not solving.
    Throughput ~210-250 rps and **does not scale with threads** (252 at
    2, 228 at 8): warm requests are GIL-bound, so scale by process.
  - **Cold scales with seats, not streets:** 9-max preflop 98.2s (37x
    heads-up preflop); heads-up river 12.3s (4.7x).
  - **Chance nodes cost 50-70x each:** `solve_flop` 0.195s -> `+turn`
    10.87s -> `+turn+river` **765.6s**, 3,926x end to end. The
    operational point is the contrast: that primitive costs 765s at 19
    combos while the river ENDPOINT serves cold in 12.29s — the combo
    caps are the only reason the river is reachable.
  - **Scaling:** iterations linear, players sub-linear, equity samples
    linear, combo pool **super-linear** (2.8x per doubling). The linear
    sample curve is why M99's equal-cost test had to hold wall clock
    fixed rather than assume `samples x iterations` was the budget.
  - **NEW DEFECT — the fold-vs-play call is not a positional range
    chart.** 40 random deals produced **zero categorical violations**
    (no premium folded, no trash played early), but measuring the
    combo-weighted opening frequency per seat found:
    | seat | shipped 3k | 12k | GTO |
    |---|---|---|---|
    | 2max BTN | **0.871** ✓ | — | 0.70-0.95 |
    | 6max UTG | 0.281 | 0.176 | 0.15-0.18 |
    | 6max BTN | 0.384 | **0.159** | **~0.45** |
    | 6max SB | 0.498 | **0.806** | ~0.45 |
    At the shipped budget the gradient is compressed (6pp across four
    seats where GTO spans ~30pp). At 12,000 **the button opens TIGHTER
    than under the gun**, on both seeds — an inversion needing no
    published chart to condemn. The 3,000-iteration ordering wobble is
    seed-dependent and is convergence noise; the 12k inversion is not.
  - **Two claims corrected in the product.**
    `SIZING_CAVEAT_REASON` said "Trust the fold-vs-play call" and now
    says it is sounder but not fully GTO, naming the positional
    weakness. CLAUDE.md's M98 entry said the fold-vs-play call "is
    unaffected and remains sound at 3/6-max" — also corrected. M98 marked
    the SIZING axis broken and treated the other half as reliable; the
    other half has its own defect, and users were being told to trust it.
  - **Verification:** 887 backend tests, 154 frontend, tsc and lint clean.

- **M111 — the positional defect sharpened, and two of M110's own claims
  corrected.** M110 measured the 6-max opening range per seat and
  reported two problems. One was real and mis-described; the other was
  not a problem at all.
  - **The SB row was not a defect.** M110 compared SB's 0.806 against a
    generic 6-max SB reference of ~0.45 and called it "wildly loose".
    When it folds to the small blind, SB is **heads-up against BB**, so
    the right comparison is the heads-up opening frequency (~0.80-0.87)
    and 0.806 is close to correct. A remembered reference applied to the
    wrong situation — the same failure M106 recorded, one milestone
    after recording it.
  - **"The button opens tighter than under the gun" was over-read.** The
    gap is 1.7pp; CO alone varies 2.8pp between seeds. Breaking the
    strategy into action mass per seat at 12,000 iterations shows what is
    actually happening — fold mass **flat at 0.82-0.84 across UTG, MP,
    CO and BTN**:
    | seat | live | trained | fold | raise | call | all-in |
    |---|---|---|---|---|---|---|
    | UTG | 6 | 1.0 | 0.824 | 0.100 | 0.064 | 0.012 |
    | MP | 5 | 1.0 | 0.829 | 0.040 | 0.099 | 0.032 |
    | CO | 4 | 1.0 | 0.826 | 0.078 | 0.069 | 0.027 |
    | BTN | 3 | 1.0 | 0.841 | 0.050 | 0.057 | 0.052 |
    | SB | 2 | 1.0 | 0.194 | 0.420 | 0.362 | 0.025 |
    The correct statement is stronger and simpler: **position is not
    learned at all** among non-blind seats.
  - **Under-training is refuted:** `trained_share` is **1.0 at every
    seat**, so the button is not starved of visits. The hypothesis was
    testable and was tested rather than assumed — and it was the more
    obvious explanation.
  - **Same root cause as the sizing defect.** M98 established terminals
    are priced `equity * pot - invested`, so *playing* is uniformly
    underpriced; if that understatement is equal at every seat, the
    fold/play boundary cannot move with position. **One cause, two
    symptoms**, and it means the positional defect also needs solved
    postflop continuation values rather than a larger budget.

- **M112 — costing the architectural fix M100 said could not be costed.**
  M98 found the root cause (terminals priced at raw showdown equity);
  M111 showed it explains the positional defect too. M100 refuted a crude
  stand-in and concluded the real fix "cannot be justified or costed on
  this evidence". This costs it — measurement, not a build. Full working
  in `docs/benchmarks-2026-08-24.md`.
  - **Naive framing says impossible.** 6-max has **15,254 showdown
    terminals with money behind** (heads-up has 7). At 0.195s per flop
    solve and 10 boards that is **8.3 hours per preflop solve** — and it
    is affordable at heads-up, which does not have the defect, and
    infeasible at 6-max, which does.
  - **Canonicalization changes the answer.** A continuation value depends
    on the game that follows — pot, money behind, live seats — not on the
    action sequence that reached it. Keying on (log2 SPR bucket, live
    count): **15,254 terminals collapse to 27 distinct spots** (4 at
    heads-up), i.e. 52.7s rather than 8.3 hours. Same observation M20's
    canonical library rests on, applied one street earlier.
  - **Cost is near-quadratic in range width**, measured: 21 combos/side
    0.272s, 66 -> 2.32s, 193 -> 14.71s, 379 -> 58.16s. 18x the combos
    costs 214x the time. At the project's existing cap (~66 combos) the
    precompute is **10.4 min** per (depth, table size), or ~3 min at
    three sampled boards — inside the ~15 min the startup pre-warm
    already spends in a daemon thread.
  - **Verdict: affordable as an offline precompute** at the cap widths
    already in use. The naive framing overstated it by ~50x.
  - **Explicitly NOT established:** whether a continuation value computed
    at *capped* ranges is accurate enough to fix anything. The caps are
    M24-era cost controls for a different purpose. M100's lesson stands —
    a mechanism producing plausible numbers is not thereby correct — and
    the validation targets are already known: AA's jam should fall from
    0.615 toward ~0.03 at 12k, and fold mass should stop being flat at
    0.82-0.84 across UTG/MP/CO/BTN.

- **M113 — the continuation-value primitive, built and validated.** M112
  costed the architectural fix and found it affordable (15,254 terminals
  collapse to 27 canonical spots, ~10 min of offline precompute). This
  builds the piece everything else needs: **what a continuation value
  actually is** — the expected value of PLAYING a spot, per hand, from a
  real solve.
  - **`poker_solver/continuation.py`, `expected_values_at_root`.** Walks
    a solved tree under the AVERAGE strategy (CFR's real output, not the
    still-oscillating current one), carrying a full (hero hand x villain
    hand) MATRIX rather than a per-hand vector — when villain acts, which
    action they take depends on THEIR hand, so collapsing villain's
    dimension early would average over a decision not yet made. The
    matrix is contracted against villain's range exactly once, at the end.
  - **Validated by INVARIANT, not by predicting the answer.** Terminal
    payoffs are `equity * pot - invested`, equities sum to 1 and the
    street's own investments cancel, so both sides' expected values must
    sum to **exactly the pot**. Holds to nine decimals on three
    configurations.
  - **Two of my own validation attempts were wrong first**, which is why
    the invariant approach was adopted. The first predicted "hand A is
    worth 6.5" from raw equity arithmetic and failed — because the tree
    offered an all-in and was never the check-down that assumed. The
    second stated the invariant as "sums to zero" and passed `equity.T`
    as villain's table; villain's equity is `1 - equity` transposed, and
    the pot is money already in the middle that both sides' EV includes.
    Neither was a bug in the walker.
  - **Mutation-checked, and it exposed a blind case.** Flipping villain's
    weighting to the hero axis (`[:, None]` instead of `[None, :]`) fails
    the asymmetric and three-hand cases and **passes the symmetric one**,
    where rows and columns are identical by construction. A
    symmetric-only test would have been blind to the exact error the
    matrix design exists to prevent.
  - Also asserted: EV is monotone in hand strength, `villain_reach` is
    normalized and genuinely used (tilting villain toward strong hands
    lowers hero's EV), and an empty villain range is refused rather than
    silently producing NaN.
  - **Not yet wired into the solver.** The precompute and the terminal
    rewrite come next, with M112's validation targets fixed in advance so
    the change cannot grade its own homework: AA's jam 0.615 -> ~0.03 at
    12k, and fold mass no longer flat at 0.82-0.84 across UTG/MP/CO/BTN.
  - **Verification:** 894 backend tests (up from 887), 154 frontend.

- **M114 — the continuation-value precompute.** M113 built the primitive
  (what a continuation value IS); this builds the table M112's costing
  called for, and the numbers carry real postflop structure.
  - **`continuation_key(pot, chips_behind, live_seats)`** — the canonical
    identity of a terminal's FOLLOWING game. Bucketed on **log2(SPR)**,
    not raw SPR: postflop play changes with the order of magnitude of the
    stack-to-pot ratio (SPR 1 vs 2 is a different game; 20 vs 21 is not).
    This is what collapses M112's 15,254 terminals to 27.
  - **`build_continuation_table`** returns EV per hand class **as a
    multiple of the pot**, so one solved spot serves every real terminal
    sharing its key — the point of the canonicalization. Boards are
    sampled, not enumerated: 1,755 canonical flops per spot is not
    affordable and the value is an average over runouts anyway.
  - **The numbers show solved structure, not programming.** At SPR ~9.5
    vs ~1.5: AA 0.775 -> **0.867** (worth MORE shallow, where opponents
    have less room to outplay it), 72o 0.281 -> **0.246** (worth LESS
    shallow, with less room to bluff), and AA > JTs > KQs > 72o at both
    depths. **[Cost claim corrected in M115.]** This entry said "~40s for
    the full 27", extrapolated from a 2-spot smoke test using only FOUR
    hand classes. At a realistic 12-class range the per-board
    `build_board_equity_table` dominates, and the real figure is
    **1,117.6s (18.6 min)** — 28x the estimate. M112's independent
    estimate of 10.4 min was much closer, off by ~1.8x rather than 28x,
    because it costed at 66 combos/side instead of extrapolating from a
    toy pool. Extrapolating cost from a fixture small enough to be fast
    is how you get a number that is wrong by an order of magnitude.
  - **The approximation is stated in the code, not buried.** The key
    carries SPR and live count and NOT range strength, so a three-bet pot
    and a limped pot at the same SPR are treated alike. That is a real
    fidelity cost and it is unmeasured; M112 flagged it as the open
    question. If validation fails, adding a range-strength dimension is
    the first thing to try, not more boards.
  - **`combo_class` moved into the engine** (`poker_solver/combos.py`,
    beside `HandCombo`) because the engine needed it and must not import
    from `api/` — the boundary is enforced by
    `tests/test_package_boundary.py`. `api/solving.py`'s `_combo_to_class`
    delegates rather than keeping a second copy.
  - **Same NaN treatment as `solve_flop`.** Conflicting (hero, villain)
    combo pairs come back NaN from `build_board_equity_table`, and a
    neutral 0.5 stands in — using a differently-prepared table would
    price the EV against a different game than the one solved.
  - **Verification:** 897 backend tests (up from 894), 154 frontend.

- **M115 — the continuation-value fix wired in, validated, and REFUTED.**
  M113 built the primitive, M114 the precompute; this wires the table
  into `_mccfr_terminal_value` and tests it against the targets M112
  fixed **before any of it was built**. Both fail.
  - **Paired across 5 seeds** (same seed both arms, so seed variance
    cancels rather than being averaged away — M100's design):
    | metric | paired mean | SEM | direction |
    |---|---|---|---|
    | AA jam delta | **+0.019** | 0.201 | fell in **2/5** |
    | fold spread delta | **+1.23pp** | 1.91pp | widened in 4/5 |
    Both null. The jam delta is indistinguishable from zero with two of
    five seeds improving; the fold spread is inside its own error bar,
    and even at face value 1.23pp against the ~30pp GTO spans is nothing.
  - **A single seed had looked like success** — AA's jam 0.4955 ->
    0.3078 and a monotone positional gradient, the first time the
    baseline had ever produced one. M110 had already measured 12k jam
    varying 0.37-0.92 across seeds, so that difference sat inside known
    noise. Flagged as unreportable *before* running the paired test, not
    after seeing it fail.
  - **What still stands:** M98's diagnosis of the CAUSE. It is read
    directly off the terminal expression, and M99 confirmed the mechanism
    postflop with a monotone 10.2pp effect across three tree depths. What
    is refuted is this particular *correction*.
  - **The likeliest reason is the approximation M114 documented in
    advance:** `continuation_key` carries SPR and live-seat count but NOT
    range strength, so a three-bet pot and a limped pot at the same SPR
    receive the same continuation value, and their ranges are nothing
    alike. M114 named adding a range-strength dimension as the first
    thing to try if validation failed. It failed; that is next — not more
    boards, not more iterations.
  - **Cost, measured twice independently:** 1,117.6s and 1,107.9s for 27
    spots x 3 boards at a 12-class range, dominated by
    `build_board_equity_table` per board. This also corrected M114's own
    "~40s" claim, which extrapolated from a 2-spot smoke test using four
    hand classes and was wrong by 28x.
  - **`continuation_table` stays, default `None`** — like
    `optimism`/`smoothing` after M97 and `continuation` after M100. A
    refuted approach that remains reproducible is worth more than one
    that is only remembered, and the machinery is the scaffolding a
    range-keyed retry would need anyway.
  - **Verification:** 897 backend tests, 154 frontend.

- **M116 — why M115 failed: the building RANGE, not just the key.**
  M115 refuted the continuation-value fix and blamed `continuation_key`
  for carrying no range-strength dimension. That named one candidate and
  did not separate it from a second: the table was BUILT against a
  uniform 12-class spread across all 169 hands, far weaker than any range
  that actually reaches a preflop showdown terminal (M108 measured a
  raiser's range weighting KK 640x above T2o).
  - **Measured, holding the canonical key fixed and changing only the
    building range** (shared core of four hands, differing only in what
    surrounds them):
    | hand | vs loose range | vs tight range | delta |
    |---|---|---|---|
    | AA | 0.957 | 0.879 | -0.078 |
    | KK | 0.639 | 0.463 | -0.176 |
    | QQ | 0.551 | 0.344 | -0.207 |
    | AKo | 0.652 | 0.421 | **-0.231** |
    Values shift by up to **0.23 of the pot** on quantities whose own
    magnitude is 0.3-1.0 — a large fraction of the signal, and every hand
    correctly worth LESS against a tighter opponent range.
  - **So the two candidates are one fix, not alternatives.** No key
    refinement compensates for a table built against the wrong game. A
    future attempt must key BY range strength *and* build each entry with
    a range of that strength.
  - **The first fixture could not see the effect.** It sampled a uniform
    spread that overlapped the raiser range on **AA alone**, measured a
    0.04 delta, and read as evidence of insensitivity. A fixture too thin
    to detect an effect is not evidence the effect is absent — the same
    shape as M105's stratification lesson, and caught the same way, by
    checking what the fixture actually compared instead of trusting the
    number.
  - Pinned by a test asserting both the magnitude (>0.10 of pot) and the
    direction (every hand worth less against a tighter range), so the
    sensitivity cannot be "optimized" away by sharing one table.

- **M117 — audit round 10: the betting tree, exhaustively.** Nine
  earlier rounds verified other layers while assuming this one. Eight
  legality invariants at every node of whole trees, 38 configs (2-6
  players, raise caps 1-4, 2bb-100bb, postflop SPR 0.2-16): **26,354
  nodes, 11,784 showdowns, zero violations**. The load-bearing one is
  **no side pots at showdown** — M23 proved it from construction and
  built `query_strategy_from_path` on it, and nothing had ever checked
  it. It holds.
  - **F29 (fixed): a stack shorter than the big blind was accepted.**
    `GameConfig` required `stack_bb > small_blind`, but the big blind is
    posted unconditionally, so a stack between the two blinds built a
    root with `invested["BB"] > stack_bb` and every pot below it counted
    chips nobody had — exactly `2 * (big_blind - stack_bb)`, which is
    **96% of the real pot at 0.51bb** and 67% at 0.6bb. `POST /advise
    {"stack_bb": 0.6}` returned **200 with a full 169-class strategy**.
    `api/schemas.py` deliberately carries no bound, relying on
    `GameConfig.__post_init__` — true, and the wrong bound. Now
    `stack_bb >= big_blind`; 0.6bb is a 422. Nobody plays 0.6bb; the
    shape is what matters, and it is F23's shape — a confident 200
    answering a question nobody asked. **Raising the bound was not the
    whole fix**: at exactly `stack_bb == big_blind` the BB is all-in from
    posting, and `_build`'s opening `to_act` listed every position
    unconditionally, so the BB got a decision node whose one action was a
    call it had no chips for. The all-in filter lived only in
    `_reopened_order` — reachable only from a raise, and no raise can
    follow an all-in (F28), so it could never have covered this.
    `build_game_tree` filters the opening `to_act` now; verified clean at
    2/3/4/6 players from 1.0bb to 100bb.
  - **F28 (documented, not fixed): `_reopened_order`'s all-in exclusion
    cannot fire.** 1,880 instrumented calls, zero exclusions. Once
    anyone is all-in, `current_bet == stack_bb`, so `to_call` and
    `remaining_stack` are equal for everyone left and `_build` requires
    `remaining_stack > to_call` strictly — no raise can follow an
    all-in, and `_reopened_order` is only called from a raise. **Kept
    deliberately**: it is the guard that would matter the moment stacks
    stopped being equal, which is the assumption the no-side-pots proof
    rests on. `test_no_raise_is_offered_once_anyone_is_all_in` pins the
    property that makes it dead.
  - **Two config guards had never been exercised.** The F29 fix failed
    its own new test, which is how this surfaced:
    `test_config_rejects_stack_not_greater_than_small_blind` passed
    `raise_sizes=()` with the default `max_raises=4` — itself invalid,
    and checked first — so it asserted a bare `ValueError` it got from
    the wrong guard. Same for `test_config_rejects_nonpositive_blinds`.
    Both now pass `max_raises=1` and `match=` the guard they mean. Swept
    the other ~70 `raise_sizes=()` uses in the suite: every one correctly
    paired with `max_raises=1`; these two were the only inert ones. Third
    time this audit has found a check that could not fail (M105, M108,
    now this), and the third time it surfaced only by making something
    fail on purpose.
  - **Mutation-tested**: a 1%-short call, a raiser re-acting against
    their own raise, and a dropped entering pot were each caught by
    thousands of violations. The fourth mutation — deleting F28's
    exclusion — was not caught, which *is* F28.

- **M118 — audit round 11: canonical strategy transport. No defect
  found.** The library's whole value proposition is that one solve
  serves every suit-isomorphic board; if the translation back into the
  caller's real suits is wrong, a hit returns a real strategy for the
  WRONG HAND and nothing downstream can tell, because the numbers look
  as healthy as they would if it were right.
  - **The gap this closes is M21's own correction.** M20 claimed a hit
    serves any isomorphic board exactly; M21 withdrew the bit-exact half
    because flop equity is sampled and the deck's suit-dependent
    iteration order draws different runouts for two differently-suited
    isomorphic boards. Right correction — and it left the crux asserted
    but unchecked, since the obvious check (compare strategy numbers) is
    exactly the one the noise ruins.
  - **What is exact: hand STRENGTH is not sampled.** A correctly
    translated combo must make the same five-card hand against the
    canonical board that the original makes against the real one.
    **16,000 (board, hero) pairs across flop/turn/river plus monotone
    and paired flops: zero round-trip failures, zero collisions with the
    canonical board, zero strength changes.** Monotone and paired boards
    are in deliberately — largest automorphism groups, and exactly where
    M19's rejected single-pass canonicalization under-collapsed.
  - **End to end**: six isomorphic boards against one stored solve give
    an *identical* multiset of strategy rows, and every returned key is
    a combo legal against the board that ASKED rather than the board
    that was solved. Swept for leaks: nothing outside `library.py` reads
    an entry's untranslated canonical-space strategy.
  - **The mutation that justifies the strength check**: replacing the
    returned `suit_map` with a DIFFERENT valid permutation leaves the
    round-trip a flawless bijection — 0 failures, so a round-trip-only
    test passes it — while tripping strength 466 times and board
    collisions 2,119 times. The other two mutations (uninverted map;
    translating one card of two) were caught 8,765 times and by crash.
    The end-to-end test was checked separately: swapping
    `lookup_strategy`'s translation for a raw `dict(entry.strategy)`
    fails on the first board, the solved one included — `Ac Kd 7h`
    canonicalizes to `7c Kd Ah`, so even it needs translating.

- **M119 — audit round 12: the 169-class <-> 1,326-combo layer. A real
  engine defect, the first this audit has found inside the solver
  rather than in a seam around it.** Every derived range passes through
  one function: a preflop solve speaks in 169 classes, every postflop
  solve speaks in concrete combos. Eleven rounds had verified layers on
  both sides of it.
  - **Clean**: the partition is exact — 169 classes expand to 1,326
    distinct combos, none claimed twice, none missed; counts 6/4/12 by
    type; `combo_class` an exact inverse over all 1,326.
  - **F30 (fixed): the conversion inverted card multiplicity.**
    `range_from_class_frequencies` divided a class's frequency across
    its combos, giving every class equal total mass regardless of size.
    Two proofs, neither needing a solve:
      * **The whole deck was not uniform.** With every class continuing
        at 1.0 — nobody has folded, so the range IS the deck — 312
        suited combos came back at 0.250, 78 pairs at 0.167 and 936
        offsuit at 0.083. The model believed **AhKh was three times as
        likely as AhKs**.
      * **Blockers were cancelled exactly.** AA kept mass 1.0 whether 6,
        3 or 1 combo survived; on a two-ace board one combo carried the
        weight of six. Card removal is the stated reason postflop works
        in combos at all.
    The fix follows from the input: `derive_ranges_from_path` returns
    CONDITIONAL frequencies (P(line | class), 1.0 for a position that
    has not acted) against a uniform prior over combos, so a combo's
    weight is just its class's frequency — nothing to divide. Measured
    through the real pipeline (40bb, open-call, 9h8h2c): **18 of the top
    24 combos change**; aggregate flop strategy moves all-in
    **0.245 -> 0.167**, call **0.562 -> 0.622**. Three tests had
    asserted the defect as the contract ("weights sum to the input
    frequency") and now assert the property that makes it correct.
  - **F30 was hiding the river cap collapsing onto one class.**
    `_cap_range_to_combos` takes top-N combos by weight; the old suited
    bias spread that selection across classes BY ACCIDENT. With correct
    weights the most frequent class swallows the budget — at the shipped
    cap of 9, a real river spot returned **nine combos of one offsuit
    class**, mean hand category 0.0. Now round-robin across classes in
    frequency order: nine classes, mean category 0.889, identical solve
    cost. Written first with `str(class)` as tie-break, which is worse —
    alphabetical ties put 22 and 32o ahead of AA; a stable sort on
    frequency alone keeps canonical order.
  - **And a river fixture that could not test what it claimed.**
    `FAST_RIVER_PATH_QUERY_MAX_COMBOS_PER_SIDE = 1` is not a small
    range, it is no range — one fixed hand against one fixed hand, where
    `test_advise_river_decision_discriminates_by_hand_strength` is
    decided by which single villain combo the cap picks. It had been
    passing on that coin flip. **At production settings the behaviour is
    strongly correct and always was**: facing a river all-in, 5c4d folds
    0.971, 9s9d 0.018, AsKs 0.428, 7c3h 0.995. Raised to 3 — river
    subset costs cap 1 = 33.6s, cap 3 = 45.0s, cap 6 = 83.4s.
  - **One of my own claims was a test artifact**: the first pass
    reported the corrected weights making the cap select alphabetical
    trash (`2d2c`, `3c2c`). That was my probe imposing `str(combo)` as a
    tie-break, not the code — the real stable sort keeps AA first.
    Caught before it became a finding.

- **M120 — validating F30 at the shipped operating point.** CLAUDE.md
  requires an end-to-end `/advise` check at production settings after
  any solver change, and M119 changed what every postflop solve is
  handed.
  - **The property F30 was argued from is real.** Its justification was
    that dividing a class's frequency cancelled card removal exactly, so
    a class's share of a range must now fall as the board takes its
    cards: AA's share goes **0.400 (no aces) -> 0.200 (one ace) ->
    0.077 (two aces)**, where the old weighting held it CONSTANT at
    every board. A fix that moved numbers without producing the
    behaviour it was argued from would not be a fix.
  - **Postflop advice still classifies hands correctly**: 15 random
    flops at production settings, hero facing a real flop bet, top set
    vs air — **zero violations**, top set folds 0.0 on every board, air
    folds 0.70-0.9999 on most. Two boards show air folding less (0.178
    on AcJs4d, 0.703 on 6c4hJc), consistent with the documented M98/M99
    terminal-pricing flaw that underprices playing; not a new defect.
  - **Two versions of this sweep measured nothing, and neither could
    have failed.** Version 1 asked about the flop's OPENING decision,
    where there is no bet to fold to — so "continue mass" was pinned at
    exactly 1.0 for every hand and the assertion was incapable of
    failing; it reported 25 clean trials of nothing. Version 2 used "any
    hand pairing the board" as the strong hand, which with the deuce
    kicker the generator picked is usually BOTTOM pair — a hand that
    correctly folds to a big bet, so the comparison was never
    strong-vs-weak. An earlier draft skipped all 25 trials outright and
    still printed `violations: 0`; it was caught only because the skip
    count was printed beside the trial count.
  - **Sixth inert check this audit has met** (M105, M108, M117 x2,
    M119's probe artifact, now these). Stated as a rule: *a clean result
    is not evidence until the check has been shown capable of returning
    a dirty one*, and printing the denominator is the cheapest proof.

- **M121 — audit round 13: chance nodes. No defect, and an
  approximation that had been asserted since M12 is finally measured.**
  Chance nodes are how turn and river cards enter the tree, and the area
  has form: M75 found multiway turn/river returning 0 of 132 combos
  trained, every strategy exactly uniform, since the feature shipped.
  - **Structural: 24 chance nodes across four boards x two stack depths,
    zero violations.** The branch set is exactly `remaining_deck(board)`,
    no board card is ever dealt, every branch is filed under its own
    card, every equity table has the pool's shape. Branch probability is
    implicit — `cfr._solve_recurse` averages
    `sum(branch_values) / len(branch_values)` — so uniformity follows
    from the branch SET being right, which is why the set is what gets
    pinned.
  - **The M12 approximation, measured.** `remaining_deck` excludes only
    the board, so some branches deal a card a combo physically holds and
    that combo's equity there becomes 0.5. M12 called this "~4.3% of
    branches per combo" that "nets toward neutral rather than a wrong
    extreme", with no number on the second claim. Measured: **exactly 2
    of 49** branches per combo (4.08%); every combo's biased equity
    stays on the same side of 0.5 and moves strictly closer to it;
    **mean |bias| 0.0053, max 0.0131 equity, signed mean ~1e-05**. It is
    a compression, not a directional error — the pool's strongest hand
    (0.822) is dragged down 0.0131 and weak hands pushed up.
  - **The deferred per-branch-masking fix stays deferred, now for a
    stated reason.** A 1.3pp ceiling is an order of magnitude below the
    ~5pp-per-street flop terminal-pricing distortion M99 measured and
    deliberately chose not to surface. The threshold is written into the
    test, so a materially higher bound would re-open it.
  - **A wrong finding, caught before write-up.** The first probe
    reported "0 impossible rows marked NaN, 16 not" and looked like
    proof the documented mitigation was absent. It was reading the table
    AFTER `nan_to_num` — the conversion the note itself describes; a
    direct `build_board_equity_table` call returns NaN exactly as
    documented. Seventh inert-or-wrong check this audit has produced and
    the first that would have been a FALSE POSITIVE rather than a false
    clean. Same remedy: check what the number measures before believing
    it.
  - **Mutation-tested**: dealing from the full deck (4 failures) and
    letting impossible rows keep a real number instead of 0.5 (1
    failure) are both caught.

- **M122 — audit round 14: response shaping. No numeric defect; one
  stale claim that argued for removing a safety signal.**
  `strategy_format.py` is the seam between a solved `StrategyResult` and
  what a caller reads.
  - **Clean**: swept live across `/solve` at 2/3/6 players and
    `/solve_flop_cached` — **532 strategy rows, zero violations**. Every
    hand's frequencies sum to 1.0, every value inside [0, 1], and
    `trained` covers exactly the hands `strategy` does.
  - **F31 (fixed): `format_flop_response`'s docstring had gone stale in
    the one place it matters.** It said every postflop solve was
    heads-up and exact, "so this is currently always all-`True` in
    practice", and named multiway postflop as a still-unscoped future
    gap. **M35 closed that gap**: `solve_flop_multiway` is sampled
    MCCFR, and a three-position flop solve formatted through this exact
    function returns `trained` containing both `False` and `True`
    (measured). A reader trusting the old text would conclude the field
    was decorative here and could be dropped for tidiness — precisely
    what CLAUDE.md forbids, since `trained` exists because output can
    look confident and be fabricated. The comment was quietly arguing
    for removing a safety signal on the one path where it had become
    load-bearing.
  - **M96's drift detector structurally could not catch it.**
    `test_no_comment_claims_a_call_its_function_does_not_make` checks
    that a comment naming a CALL is not lying about the code beneath it.
    F31 is a claim about BEHAVIOUR — the code it describes never
    changed; a different module did. Widening the detector to
    behavioural claims would mean executing what each comment describes,
    which is what tests are, so the durable answer is the new test, not
    a smarter parser.
  - **Mutation-tested**: forcing `trained_for_position` to return
    all-`True` fails the new guard with `got [True]`.

- **M123 — audit round 15: the honesty signals, as a user receives
  them.** The engine can be right and the product still mislead. Every
  earlier round checked whether the engine was correct; this one checked
  what a person is actually told.
  - **The delivery chain is sound.** Measured end to end: heads-up high
    on both axes, 3- and 6-max `sizing_confidence: low` with a reason,
    9-max low on both with reasons, flop deliberately unflagged (M99
    measured the flop analogue at ~5pp per street and chose not to
    surface it, since flagging every postflop response would devalue the
    preflop warning). The API emits exactly `"low"`/`"high"` and
    `AdviseSolver.tsx` gates on `=== 'low'`, so no value drift; the
    frontend suite already covers rendering each caveat.
  - **F32 (fixed): the caveat repeated a measurement the project had
    withdrawn.** It told users "at 6-max the button has measured TIGHTER
    than under the gun" — M110's claim, which **M111 withdrew in the
    same milestone that sharpened the finding**, the 1.7pp gap being
    smaller than the 2.8pp CO varies between seeds. M111 edited this
    string (removing "trust the fold-vs-play call") and left the
    over-read standing. It now states M111's actual result: among
    non-blind seats position is not learned at all, fold mass flat at
    0.82-0.84 across UTG/MP/CO/BTN, against a GTO reference widening
    from ~15% under the gun to ~45% on the button.
  - **Every internal record was already correct** — CLAUDE.md, this log
    and the constraint notes all carry M111's retraction. The one place
    the withdrawn number survived was the sentence a person reads while
    deciding how to play a hand, which is the only copy that changes
    anybody's behaviour. Internal accuracy does not propagate outward on
    its own.
  - Guarded by SHAPE, not phrasing: the caveat must describe flatness
    and must not assert any seat opens tighter/looser/wider than
    another — the form of claim that was retracted, not just its
    wording.

- **M124 — maximal diagnostic, and all five recommendations acted on.**
  A whole-project pass after audit rounds 1-15: static structure,
  mechanically re-verified constraints, live behaviour at production
  settings, latency normalized against a same-run reference, coverage,
  and the product surface. Full report in
  `docs/diagnostic-2026-08-24.md`.
  - **Headline: the engine is in good shape; the product's worst problem
    was latency, not correctness.** Every correctness probe came back
    clean — 40 random deals answered with zero malformed responses, all
    four documented defects still behaving as documented, 8 of 9
    checkable CLAUDE.md constraints verified directly, **95% line
    coverage** (2,785 statements, 131 missed), frontend lint/typecheck/
    build clean with all 13 components tested.
  - **D1 (fixed): the multiway preflop cache was keyed at 1bb
    granularity** on the most expensive solve in the product — measured
    cold at **66s (6-max) and 93s (9-max)** — while the pre-warm covered
    three depths of ~200 plausible ones. Now a 5bb FLOOR bucket, with
    the solve running at the bucketed depth (keying on the bucket while
    solving at the real depth would serve a 99bb tree to a 95bb player:
    F13 exactly). Through the real API: 97bb cold 28.3s, then **98bb and
    99bb at 2.4-2.8ms**; affordability swept at six depths, zero
    violations.
  - **D1 was adopted on a CONTROL, and nearly became a wrong finding.**
    Raw bucket-vs-truth numbers looked alarming (fold frequency moving
    up to 0.89, eight hands flipping fold/play). The control —
    re-running the SAME depth under a different seed — moves the
    strategy just as much, and more at 99bb (12 flips vs 10). Without it
    this reads as depth sensitivity and the fix dies; it is the same
    over-read M110 made and M111 corrected.
  - **The control surfaced something uncomfortable, recorded not
    buried**: 8-12 of 169 hands cross the fold/play line between two
    runs differing only in seed. Bucketing is free BECAUSE the solve is
    already that noisy, not because strategy is depth-insensitive. The
    66s a user waits buys an answer that would partly differ if re-run.
    Consistent with M73/M74/M111's documented instability, seen from a
    new angle.
  - **D2 (fixed): the pre-warm was 0% covered and failed silently** —
    the largest uncovered block in the project, seven duplicated
    `except Exception: logger.exception(...)` blocks in a daemon thread
    nobody joins, and it is the ONLY mitigation for D1. A config typo
    would have looked like "the product is slow". Now one
    `_prewarm_step` helper recording every outcome in `PREWARM_STATUS`,
    with two mutation-checked tests. Same failure shape as F25 (M107).
  - **D3 (fixed): the unbounded-cache exemption asserted its own
    premise.** `test_no_solve_cache_is_unbounded` exempted
    `multiway_equity` because its key is a config constant — true, and
    unchecked. It now inspects every call site of
    `_get_multiway_equity_cache` and fails unless each passes a `cfg.`
    constant. Mutation-checked.
  - **D4 (fixed): coverage measures execution, not assertion.**
    `api/solving.py` sat at 94% line coverage with 20 of 31 functions
    never named in a test — and `_cap_range_to_combos` ran on every
    river request and still shipped M119's defect. Nine direct property
    tests added for `_cap_range` and `_infer_street`. The remedy was
    never "raise coverage".
  - **D5 (fixed): `MAX_ITERATIONS` had no justification** — the only
    such constant left. Measured: 1,000 iters 2.8s, 5,000 12.1s, 20,000
    **50.0s**, the same bracket its sibling caps were set against.
  - **One would-be finding discarded before reporting**: a first pass
    flagged "25 of 48 config constants lack measurement", which was the
    heuristic misreading shared comment blocks and `= DEFAULT_X`
    aliases. Real number: one.

- **M125 — second maximal diagnostic, and all three findings acted on.**
  Run immediately after M124, deliberately targeting ground it did not
  cover: cross-endpoint agreement after F30, library persistence,
  concurrency, stack extremes, the dev proxy, response-schema
  completeness, and **the running frontend in a real browser** — which
  no diagnostic in this project had ever done. Report in
  `docs/diagnostic-2026-08-24b.md`.
  - **Correctness came back clean everywhere it was checked.**
    Cross-endpoint agreement survived F30 exactly — preflop 169 hands,
    flop 127 combos, turn 57 combos, **max delta 0.0** at every street,
    same position and pot on both sides. Library save/load round-trips
    exactly. Stack extremes 1bb-10,000bb all correct with no
    unaffordable bets and exact row sums. The frontend runs with **zero
    console errors**, and M123's corrected caveat renders on screen
    verbatim.
  - **All three findings were about what the product TELLS a user, not
    what it computes** — the third consecutive diagnostic where that was
    true.
  - **E2 (fixed): the 9-max range chart carried no confidence signal.**
    `solver_confidence`/`sizing_confidence` existed on `AdviseResponse`
    alone — one of eleven response models — while `GET /solve?players=9`
    served a confident-looking 169-class chart of an under-trained solve
    with nothing saying so. That endpoint is what the frontend's own
    Preflop Ranges tab calls, and CLAUDE.md says plainly "Don't present
    9-max advice as authoritative". Now carries both, with a test
    asserting the two endpoints AGREE so they cannot drift.
  - **E3 (fixed): the frontend's multiway caveat was factually wrong.**
    It said the chart was "a small curated hand subset (MCCFR), not the
    full 169-hand exact solve" — M67 replaced that 8-class pool with all
    169. Wrong in the reassuring direction, blaming pool size when the
    cause is sampling variance, and lumping 9-max in with 3/6-max. **A
    second stale claim surfaced while fixing it**: `AdviseSolver.tsx`'s
    fallback still read "The fold-vs-play call is sound", the exact claim
    M111 withdrew and M123 corrected server-side two milestones earlier.
  - **E1 (fixed): the dev proxy invariant was unenforced.** All 16
    routes were covered, but the config's own comment records this
    breaking three times (M10 `/equity`, M25 `/preflop_walk`, M56
    `/advise`) and explains why nothing catches it — the frontend suite
    stubs `fetch` and structurally cannot see a proxy gap. A Python test
    now reads both the route table and `vite.config.ts`;
    mutation-checked by adding an uncovered route.
  - Backend 948 passed, frontend 156 passed.

- **M126 — the same stale claim, in a third file.** Verifying M125's E2/E3
  fixes in a browser surfaced the claim once more.
  `TableModeControl.tsx` labelled its buttons `3-max (demo)` /
  `6-max (demo)` / `9-max (demo)`, with a docstring describing "a
  multiway demo (3/6/9-max, small curated hand subset, MCCFR)".
  - **Same two problems as E3.** The pools stopped being demos in M67
    (all 169 classes), and a uniform suffix flattened a distinction the
    engine draws sharply — 3-max and 6-max are "in much better shape",
    9-max is the one that must not be presented as authoritative.
  - **Verified rather than assumed, and deliberately scoped**:
    `MULTIWAY_PREFLOP_HANDS` is 169, while the FLOP demos really are
    curated (`DEMO_FLOP_HERO_CLASSES` 3, `DEMO_FLOP_VILLAIN_CLASSES` 4,
    `DEMO_MULTIWAY_FLOP_CLASSES` 3). **The flop solvers' "(demo)" labels
    are accurate and were left alone** — only the preflop path had
    outgrown the word. Eight tests asserted the old labels and were
    pinning the inaccuracy; updated.
  - **Four files carried the same withdrawn or outdated claim**:
    `api/config.py` (M123), `PreflopRangesPage.tsx` and
    `AdviseSolver.tsx` (M125), `TableModeControl.tsx` (here). Each was
    found only by looking at the next surface out.
  - **Verified in the browser.** The 9-max chart, which carried nothing
    at all before this work, now leads with "Low confidence. 9-max
    preflop does not converge at any affordable budget…" followed by the
    sizing caveat.
  - **A probe that did not finish, reported rather than dropped.** The
    concurrency/memory probe was stopped: its design was wrong for the
    question (40 distinct stack depths made every request a COLD solve,
    measuring cold-solve cost rather than steady-state memory) and it was
    saturating the machine. Concurrency is already covered by existing
    guards; **sustained-load memory is genuinely still unmeasured** —
    nothing has touched it since M93, whose number predates every cache
    becoming bounded.
  - Backend 948 passed, frontend 156 passed.

- **M127 — a simulated player found what fifteen audit rounds could
  not.** 120 hands of real play against the live API (1.2 simulated
  hours, 275 advised decisions), dealing cards, asking at every street,
  and ACTING on the advice so the hand continued down the recommended
  line. Report published as an artifact.
  - **The engine never gave a wrong answer.** Zero invalid
    distributions, zero unaffordable bets, zero premium folds, across
    275 decisions. **The one flagged decision was my check being
    wrong**: 84o in the BB facing a 2.5x button raise heads-up, advised
    to call 99.98%. Verified — 84o has **30.3%** equity against a spread
    of button openers and needs ~30% to call 1.5 into 5, against a
    heads-up button opening ~87% of hands. Folding is the leak. The rule
    encoded a full-ring intuition and misfired heads-up.
  - **F33 (fixed): cache ceilings bounded entry COUNT, never bytes.**
    Sustained play grew the working set **linearly at 1.4 MB/s with no
    plateau** — 1,642 MB to 4,244 MB — putting a real server past 8 GB
    inside two hours. Measured per entry: `river_path` **38.45 MB**
    (180x a preflop entry) at a ceiling of 128 was a **4.9 GB** cache on
    its own; `turn_path` 7.95 MB x 128 = 1.0 GB; `flop_turn` 337 MB;
    `multiway` 157 MB. Four caches over budget, **6.4 GB combined**.
  - **Re-derived against a measured 160 MB per-cache budget** (160 not
    128 so `multiway` keeps 64 entries — the most expensive solve in the
    product at 66-93s each). river_path 128 -> 4, turn_path 128 -> 20,
    flop_turn 128 -> 60. **Re-running the same session: growth fell to
    0.029 MB/s and plateaued at ~997 MB — a 48x reduction, and the shape
    changed from a straight line to flat.**
  - **M93/M104 established "every cache is bounded" and M124 re-verified
    it — correctly.** The bound is on entry count; nothing ever asserted
    the ceilings were sized against what an entry COSTS. The new test
    measures a real entry and fails if any cache's ceiling exceeds the
    byte budget; mutation-checked by restoring river_path to 128, which
    fails with `river_path: 38.45 MB/entry x 128 = 4921 MB`.
  - **The deeper fix deliberately not taken**: a river entry is 38 MB
    because it retains the whole flop->river StrategyResult — tree,
    node_data, equity tables — when callers only read strategies off it.
    Storing less would buy back far more than trimming the count. That
    changes what the cache holds; this is the bound that stops the
    bleeding.
  - **Still open, measured not fixed.** Advice takes **6.23s median,
    19.19s p90, 40.5s p99**, with 154 of 275 decisions over 5s against a
    15-30s decision clock. And the 10-class postflop cap is a cost
    control that doubles as a STRATEGY control: a flopped set is advised
    to raise **0.25%** at the shipped cap versus **40.2%** at 26 classes.
    Controlled — solving twice at the same cap gives delta exactly 0.0,
    so the swing is range width, not noise.
  - **Limits of the session, stated rather than buried**: multiway
    postflop is under-represented because the simulator built preflop
    lines that did not close the betting round at 6-max and the API
    correctly refused them (19 of 20 recorded failures — the error
    message named the problem and the fix). Latency was measured on a
    machine also running other work, so it is an upper bound.

- **M128 — deep maximal diagnostic, and a correction to M127.** M124/M125
  measured structure, constraints, endpoints and the browser; M127 then
  found a 4.9 GB cache by PLAYING. So this one asked, per subsystem:
  what does the existing guarantee literally promise, and what does a
  real caller need? Report in `docs/diagnostic-2026-08-25.md`.
  - **Six checks, no new defects.** Evicted entries are genuinely freed
    (weakrefs die — the ceiling is real, not cosmetic); `_key_locks` is
    pruned with its entries (0 after 200 distinct keys at maxsize 4);
    repeated **cold** solves agree to delta **0.0**; board card ordering
    is irrelevant to delta **0.0**; and M127's cache fix holds with
    nothing over budget and a **530 MB** combined ceiling. The first two
    matter because of M127: a byte ceiling is worthless if eviction does
    not release, or if something leaks beside the capped thing.
  - **Two near-misses caught before reporting.** Pool size varying with
    hero's hand (131/136/133) is correct blocker behaviour; returned
    combos sharing hero's cards is correct too — `strategy` is the
    ACTING SEAT's own range, so its members are alternative holdings,
    not simultaneous ones. Eighth and ninth time this audit that a check
    needed checking before its result meant anything.
  - **F34: postflop value-hand aggression is unstable against the range
    cap** — and this **corrects M127**. M127 compared caps 10 and 26,
    saw 0.25% against 40.2%, and called it a systematic slow-play bias.
    Sweeping nine caps shows it is not a direction at all:
    a set measures .003 .004 .019 .025 **.771** .071 .122 .393 .402 and
    an overpair reverses direction **five times**. Both spike at 18
    classes and collapse. **Not noise** — same-cap solves differ by
    exactly 0.0. **Same error shape as M110 -> M111**: a two-point
    difference read as a trend.
  - **Widening is not an escape**: cap 26 takes one flop decision from
    10.8s to **52.1s**, 4.8x. No setting is both affordable and stable.
  - **So the response is M98's**: tell the user.
    `POSTFLOP_AGGRESSION_CAVEAT_REASON` ships on every postflop
    `/advise` response as `aggression_confidence`, rendered in
    `AdviseSolver.tsx`. **Scoped to the aggression axis deliberately** —
    the fold-versus-play call held across 275 advised decisions in M127,
    and a test asserts the caveat does not over-claim onto it.
  - Backend 954 passed, frontend 157 passed.

- **M129 — speed, measured: p90 cut 2.2x.** M127's play session put the
  advisor at 6.23s median and 19.19s p90 against a 15-30s decision clock.
  Profiling first (M47/M67 both record optimising on profiler intuition
  and getting nothing), then two changes.
  - **A — the Monte Carlo runout sampler is vectorised.** The inner loop
    called `rng.sample` 1.5M times per cold flop request and filled
    arrays with a Python loop, all inside the O(N^2) pair loop.
    Replaced with one NumPy draw (`argpartition` on a random key matrix)
    and broadcast assignment. Interleaved A/B in one process: **1.32x on
    the table itself, 1.14x end-to-end.** Statistically equivalent
    rather than bit-identical — the RNG stream changes, so at 4,000
    samples the mean cell moves 0.0055 — and still fully deterministic
    per seed.
  - **B — chance-branch equity tables are built in parallel.** A turn
    node builds ~49 and a river solve ~2,400, each a pure function of
    (board, combos). `build_chance_node` now builds them as a batch
    through an injected `equity_batch_fn`; `api/parallel.py` supplies a
    process-pool version. **Injected rather than implemented in the
    engine** — `poker_solver/` stays a plain library, and the default is
    sequential, byte for byte.
  - **Workers is 8, not 24, and that is measured**: 4 -> 2.57x,
    8 -> 3.38x, 16 -> 2.84x, 24 -> 2.34x. It peaks at 8 and DECLINES —
    each branch is tens of milliseconds, so past that the pool spends
    more on dispatch than the work is worth. This machine has 24 logical
    cores, so "one worker per core" would have been the worst setting
    tried.
  - **Measured on one spot, clean baselines**: flop 10.30 -> 8.55s
    (1.20x), turn 21.25 -> 12.01s (1.77x), river 15.20 -> 7.73s (1.97x).
  - **Measured in play**, same seed and hand count as M127's session:
    | metric | M127 | M129 |
    |---|---|---|
    | p50 | 6.23s | **4.28s** |
    | p90 | 19.19s | **8.71s** |
    | over 5s | 154/275 (56%) | **107/284 (38%)** |
    | session wall | 2134s | **1202s** |
    Turn median 17.32 -> **8.19s**, river 11.55 -> **4.90s**.
  - **Two corrections to my own measurements.** The profiled wall times
    first reported (flop 15.1s, turn 28.1s, river 25.2s) were **inflated
    ~39% by cProfile itself**; true baselines are 10.3/21.3/15.2s.
    Caught because A's apparent 1.58x did not match the 1.15x the
    profile's own attribution predicted. And "25-35% off the flop" from
    reading the profile measured 1.14x — the `random.sample` share was
    16% and I over-read it.
  - **The remaining tail has one identified cause**: p99 41s and max 65s
    are unchanged, and all three decisions over 30s were **cold 6-max
    preflop solves**, which have no chance branches and so neither change
    touches. They were at pre-warmed depths — the simulator uses
    `TestClient(app)` without a context manager, so lifespan never runs
    and the pre-warm never fires; a real deployment would have had those
    warm.
  - **Still short of a 3s target**, and the biggest remaining lever is
    blocked: cost is near-quadratic in combo-pool size, but the pool
    comes from `MAX_PATH_QUERY_CLASSES_PER_SIDE` — the parameter M128
    measured as moving value-hand advice erratically. It cannot be spent
    on speed without making advice worse unpredictably.

- **M130 — why postflop aggression is wrong: the cap drops the
  opponent's premiums.** M128 measured the instability and could only
  say the answer moved erratically with a cost knob. The mechanism is
  now measured, and four candidate fixes are closed off with numbers.
  - **Not under-convergence.** The obvious hypothesis — a fixed 1,000
    iterations spread over a bigger game — is dead. At 1k/4k/16k
    iterations the caps give {10: .003, 18: .694, 26: .468} ->
    {.000, .685, .518} -> {.000, .633, .493}: each cap CONVERGES to its
    own stable value and the spread does not close. Each cap is a
    different game, not a noisier one.
  - **The shipped cap is far too passive — but only ONE of the two hands
    swept actually converges, and that distinction is load-bearing.**
    At caps 10/18/26/34/44/60 on 2h6d9c:
    | hand | 10 | 18 | 26 | 34 | 44 | 60 |
    |---|---|---|---|---|---|---|
    | 9s9d set | .003 | .694 | .468 | .301 | .336 | **.347** |
    | QdQh overpair | .004 | .482 | .040 | .093 | .237 | **.125** |
    The set settles near **0.35** from cap 34 on, ~140x the shipped
    0.003. **The overpair does not settle at all** — it is still moving
    at cap 60. So "the answer is too passive" is established for both
    (every value from cap 18 up is 10-170x the shipped one), while "the
    correct answer is X" is established only for the set. Do not quote a
    convergent target for hands other than the one measured.
  - **The mechanism.** `_cap_range` ranks classes by how often they took
    the observed action. Premiums MIX — at 100bb the raiser's AA raises
    0.495 of the time because it also jams, KK 0.765, AKo 0.208 — while
    mediocre hands raise purely at 0.99+. The tenth-place cut is 0.9912,
    so every premium falls below it. **In 5 of 6 measured (stack,
    position) cases the raiser's modelled range contained no premium
    hands at all.** With no big pairs to value-bet against, value hands
    check. (The big blind's exclusion is correct and unrelated: premiums
    3-bet rather than call, so their calling frequency genuinely is 0.)
  - **Four fixes measured and rejected — don't re-try these.** Target is
    ~0.35; shipped frequency-ranking gives 0.0025.
    | rule | result | why it fails |
    |---|---|---|
    | mass = freq x combos | **0.775** | keeps only 12-combo offsuit classes; drops every pair and suited hand |
    | stratified by board strength | 0.014 | hand CATEGORY lumps AA with 44 on a dry board |
    | reserve 3 strongest | 0.0002 | displaces 12-combo classes, shrinking the pool 130 -> 117 combos |
    | reserve 5 strongest | 0.0004 | same, 130 -> 106 |
    They miss in *both* directions, from 0.0002 to 0.775. **No top-K by
    any single scalar represents a range** — a range is a distribution
    over strength, and ten classes cannot carry the shape of 169.
  - **What is left**: widen the cap (cost is quadratic and measured —
    cap 34 is 81s against cap 10's 9s) or change the representation
    (bucketing, M17's shelved machinery, whose goal here would be
    representativeness rather than speed). Both are milestones of their
    own.
  - **The caveat now names the mechanism and the DIRECTION.** "Unreliable"
    left a user nowhere to go; "the model is missing big pairs, so it
    leans too passive with strong hands" lets them discount the right
    way. Pinned by a test that fails if premiums ever survive the cap —
    because then the caveat would describe a mechanism that no longer
    applies.

- **M131 — the budget was being spent in the wrong place.** M130 closed
  off four ways to pick a better 10-class range and concluded no top-K
  by a single scalar can represent a distribution. This is a fifth idea
  of a different kind: not a better selection rule, but a REBALANCE
  between knobs that already exist.
  - **The insight follows from M130's own finding.** A postflop solve
    costs roughly (combo pairs x equity samples). The shipped setting
    bought 200 samples of precision for a 10-class slice whose
    COMPOSITION M130 measured as wrong — no premiums in the raiser's
    range. Precision behind a wrong range is wasted; trading it for
    width should be close to free.
  - **Measured across five (board, hand) spots**, each against its own
    widest-affordable reference (cap 60, samples 200, ~175s per spot):
    | cap | samples | iters | mean err | max err | wall |
    |---|---|---|---|---|---|
    | 10 | 200 | 1000 | 0.0944 | 0.3448 | 8.4s |
    | 18 | 90 | 700 | 0.1366 | 0.3849 | 11.7s |
    | 22 | 45 | 600 | 0.0527 | 0.1919 | 12.0s |
    | **26** | **30** | **500** | **0.0319** | **0.1387** | **14.7s** |
    | 26 | 30 | 1000 | 0.0199 | 0.0948 | 21.1s |
    Shipped the knee: **mean error 3x better, worst case 2.5x better**,
    at 1.75x the cost. A flopped set on 2h6d9c was advised to raise
    **0.3%** of the time against a ~35% reference; it now returns 48.6%.
  - **Width is NOT monotonically better** — cap 18 measures WORSE than
    the old cap 10 on both mean and max error. Swept several points
    rather than assuming a direction, which is the trap M110 and M127
    both fell into.
  - **Accuracy is not free**: every arm that beat the old setting cost
    more time. The knee was chosen deliberately, giving up the last
    0.012 of mean error rather than handing back all of M129's speed
    work. The user chose this point from the measured frontier.
  - **The caveat had to stop naming a direction, and its guard caught
    it.** M130's text said the advice "leans too passive with strong
    hands — lean more aggressive than advised". After the rebalance all
    five spots lean the OTHER way (a set now 0.486 against a 0.347
    reference), and four of the five errors are under 0.01. Telling a
    player to correct in a direction that has flipped is worse than
    telling them nothing, so the caveat now names the mechanism and says
    the residual has no consistent direction. The test asserting a
    direction was rewritten — its premise was measured false, which is
    exactly what it existed to detect.
  - **The mechanism is unchanged.** Premiums are still excluded at cap
    26 (`test_the_range_cap_drops_premiums_the_raiser_actually_holds`
    still passes), so the rebalance reduced the error without removing
    its cause. The structural fix M130 named — a different
    representation — is still open.
  - **The play-level cost is larger than the per-spot frontier showed,
    and that is worth stating.** The frontier measured one flop decision
    (8.4s -> 14.7s). Across a 60-hand session the flop median went
    5.16s -> 11.13s and **p90 8.71s -> 16.84s**, because flop decisions
    are a large share of postflop volume. Turn (8.19 -> 7.74s) and river
    (4.90 -> 4.83s) are untouched — they run on different constants.
    p50 moved only 4.28 -> 4.86s, since preflop dominates by count and
    is cached. Anyone weighing this tradeoff should look at p90, not the
    per-spot number.

- **M132 — the flop's single equity table, split across workers.** M129
  parallelised chance BRANCHES, which a flop solve does not have — it
  builds exactly one table — so the flop never benefited, and M131's
  wider range then made it the slowest street in the product.
  - **What made it possible**: the table is now seeded PER ROW rather
    than one stream advancing across the upper triangle. With one stream,
    a pair's draw depends on how many pairs preceded it, so computing
    rows 4..8 alone differs from reading the middle of a full build.
    Per-row seeding makes a row a function of the row alone — verified
    that three different band layouts all merge bit-identically to the
    full build.
  - **4.79x on the table** (4.28s -> 0.89s at 300 combos), bit-identical.
    End to end the flop goes **14.7s -> ~11s (1.34x)**, which matches the
    arithmetic: the table is 41% of a flop request at the M131 settings,
    so 4.79x on it saves ~32%. (The mix had flipped since cap 10, where
    it was equity 66% / CFR 33%; it is now CFR 58% / equity 41%.)
  - **Rows are banded by PAIR COUNT, not row count.** Row `a_pos` owns
    `n - a_pos - 1` pairs, so equal row counts would hand the first
    worker most of the triangle.
  - **A robustness defect found in my own change.** Pool CONSTRUCTION
    succeeds on hosts whose workers then die — Windows `spawn` re-imports
    `__main__`, which fails outright when the parent was launched from
    stdin. The fallback kept answers correct, but printed **34 worker
    tracebacks** doing so, and a request that works while emitting stack
    traces reads exactly like one that is broken. A single probe at
    construction now decides once and caches it.
  - **M131's accuracy table was superseded by this reseeding, and is
    corrected rather than carried forward.** Re-measured on the same five
    spots, against references rebuilt under the new stream:
    | arm | mean err | max err |
    |---|---|---|
    | old default (cap 10/200/1000) | 0.1259 | 0.3783 |
    | shipped (cap 26/30/500) | **0.0580** | **0.1679** |
    **2.2x mean and 2.3x max — smaller than the 3x/2.5x M131 reported.**
    The win is real and the direction holds; the magnitude was inflated
    by a stream that no longer exists. The frontier's middle rows were
    NOT re-swept, so they are indicative only, and the config says so.
    The user-facing caveat now reads "about 6 percentage points on
    average and 17 at worst", up from 3 and 14.
  - Backend 965 passed.

- **M133 — the river pre-warm had never worked, and the p99 tail was my
  own harness.** Two open items closed, one of them by finding a defect
  the other exposed.
  - **The p99/max tail (41-65s) was an artifact.** M127-M131's play
    sessions used `TestClient(app)` without a context manager, so
    lifespan never ran and the pre-warm never fired. Run WITH lifespan
    and waiting on `PREWARM_STATUS`: after it finishes, **every multiway
    preflop depth returns in 0.0s** — 6-max at 100/50/20, 3-max, 9-max.
    The tail is not a production problem.
  - **But the pre-warm takes 510s**, so a restarting server does have an
    8.5-minute window where multiway is genuinely cold. That part is
    real.
  - **F35 (fixed): `solve_river_from_path`'s pre-warm had never once
    succeeded.** It asked for a flop line of
    `["raise", "call_or_check"]`, but that endpoint's tree runs at
    `FLOP_TO_RIVER_MAX_RAISES=1` with empty `FLOP_TO_RIVER_RAISE_SIZES`,
    so `_build` offers no sized raise at all — only `call_or_check` and
    `all_in`. Every attempt raised "step 0: 'raise' is not legal at this
    node". **It has been failing since it was added**, and every default
    river request paid the full ~43s cold cost the pre-warm exists to
    prevent. Now check/check, which is what the original comment wanted
    and what this tree can express.
  - **M124's `PREWARM_STATUS` is the only reason it was visible.** Before
    it, the step swallowed its own exception into a log line. A test now
    asserts EVERY step succeeds, so a pre-warm line that stops matching
    its tree fails loudly.
  - **That test caught a second, subtler thing**: the turn pre-warm's
    line is legal only because production sets `FLOP_TURN_MAX_RAISES=2`.
    Under the suite's own speed fixture, which shrinks it to 1, the same
    line is illegal. The test restores the production shape deliberately
    and says why — a pre-warm line's validity depends on config living
    somewhere else entirely.
  - **The pre-warm is partly aimed at endpoints nobody calls.**
    `fetchTurnStrategyFromPath`, `fetchRiverStrategyFromPath`,
    `fetchFlopStrategyFromPath` and `fetchMultiwayFlopStrategyFromPath`
    all have **zero non-test callers** — the deprecated routes `/advise`
    replaced — and the comments still name a `TurnPathSolver.tsx` that
    does not exist. Those two steps cost ~60s of a 510s startup for
    traffic the UI does not generate. Recorded rather than deleted: the
    routes are still public. **No high-value replacement is obvious** —
    /advise's postflop cost is board-specific and a board cannot be
    guessed, the same wall precompute runs into.
  - Backend 966 passed.

- **M134 — a fifth selection rule dies, and the sixth idea turns out to
  be blocked by plumbing rather than by evidence.** M130 killed four ways
  to pick a better capped range. Two more were tried here; only one of
  them produced a real result, and the difference matters.
  - **Stratified selection, retried properly, FAILS — record it as dead.**
    M130's stratified attempt scored a class by `best_hand_rank(...)[0]`,
    the hand CATEGORY, which on a dry board calls AA and 44 both "one
    pair" and so spreads the sample across nothing. That was a fixable
    flaw, so it was worth retrying with the full rank tuple, which orders
    AA above 44. Measured on the same five spots against a full-range
    reference:
    | arm | mean err | max err | wall |
    |---|---|---|---|
    | shipped frequency-rank (cap 26) | **0.0580** | 0.1679 | 9.1s |
    | stratified, full rank tuple (cap 26) | 0.1157 | 0.3311 | 18.4s |
    | stratified, full rank tuple (cap 10) | 0.1526 | 0.3061 | 2.9s |
    **Twice the error of the rule it was meant to beat, and twice the
    cost.** It is worse at BOTH caps, so the strength proxy was not the
    problem — the approach is.
  - **Which sharpens M130's diagnosis rather than repeating it.**
    Excluding premiums IS a measured defect. But replacing frequency
    ranking with an even spread across strength makes things *worse*, so
    the frequency signal carries real information despite biasing against
    hands that mix. "The composition is wrong" was right; "so spread it
    evenly" does not follow.
  - **The sixth idea was NOT tested, and is recorded as untested.** The
    river path caps by COMBO budget with round-robin across classes
    (`_cap_range_to_combos`, M119) rather than by top-K classes, so a
    premium does get in — with fewer combos, which is what a mixing hand
    should have. Testing it through `_cap_range` does not work: that hook
    returns CLASS frequencies and the caller re-expands each class to all
    its combos, so a 200-combo cap came back with a **1,176-combo pool**
    and 218s per spot. That is a broken harness, not a refutation.
    Testing it for real means moving the cap below the class->combo
    expansion inside `library.query_strategy_from_path`, which is a
    plumbing change, not an experiment.
  - **Five rules are now dead and one is untested-but-plausible.** The
    honest read is that this needs the structural change M130 named — a
    different representation rather than a better selection — or simply
    more budget. Guessing at a seventh scoring function is not the way in.

- **M135 — restoring the missing premiums makes the advice WORSE, which
  corrects M130's causal claim.** The sixth idea, recorded as untested in
  M134, is now tested. It fails, and how it fails matters more than that
  it does.
  - **The idea, and why it should have worked.** M130 measured that
    `_cap_range` drops every premium from the raiser's modelled range,
    because it ranks classes by how PURELY they took the observed action
    and premiums mix. Capping by COMBO budget with round-robin instead
    lets a mixing class appear with fewer combos rather than none.
    Verified structurally: 1,176 combos trimmed to 300 keeps **all four
    premiums and all 169 classes**, exactly the composition M130 said was
    missing.
  - **Measured on the same five spots against a full-range reference:**
    | arm | mean err | max err | pool | wall |
    |---|---|---|---|---|
    | shipped, 26 classes | **0.0580** | 0.1679 | 333 | 15.1s |
    | all classes, 200 combos | 0.2403 | 0.6640 | 227 | 6.7s |
    | all classes, 333 combos | 0.1106 | 0.2857 | 338 | 14.0s |
    | all classes, 480 combos | 0.2571 | 0.6661 | 504 | 36.4s |
    **At matched pool size (333 vs 338) it is 2x worse**, and it is
    non-monotone in budget.
  - **So premium exclusion is a CORRELATE, not the cause.** M130 wrote
    "with no big pairs to value-bet against, value hands check" and
    presented it as the explanation. The mechanism it describes is real
    and still verified by
    `test_the_range_cap_drops_premiums_the_raiser_actually_holds` — but
    the direct remedy makes things worse, so it cannot be the cause of
    the error. **Do not spend a milestone on "put the premiums back":
    it has been tried and measured.**
  - **What the six failures now suggest**, stated as a hypothesis rather
    than a result: coherent-but-narrow beats diverse-but-thin. A range of
    ~26 classes at near-full frequency looks like a consistent set of
    holdings; a round-robin or stratified sample of all 169 classes with
    one to three combos each has the right diversity but is a thin smear,
    and no real opponent holds that. Both alternatives that deliberately
    restored diversity — stratified (M134) and this one — came out about
    2x worse, which is the shape that hypothesis predicts. It has NOT
    been tested directly.
  - `combo_cap_fn` is kept on `build_library`/`query_strategy`/
    `query_strategy_from_path`, defaulting to None, and `_cap_combo_range`
    kept alongside `_cap_range` — the same "keep the refuted knob at its
    default so the result stays reproducible" discipline `mccfr_solve`
    already applies to `optimism`, `smoothing` and `continuation`.

- **M136 — a seventh rule dies, and seven failures are now enough to
  call this structural.** M135's round-robin cap gave every class about
  the same number of combos, which violates M119's own finding that a
  class's mass is frequency x combo COUNT — pairs (6 combos) and offsuit
  hands (12) both got two, so offsuit hands were ~6x under-weighted.
  That is a concrete, diagnosed flaw, so the corrected rule was worth
  one test.
  - **Proportional sampling** allocates combos in proportion to each
    class's real mass, largest-remainder so small classes are not all
    rounded away. It does what it claims — at a 333 budget it gives AKo
    four combos where round-robin gave two, while AA and AKs get one
    each — and still keeps all 169 classes.
  - **It is the worst rule tried yet:**
    | arm | mean err | max err | pool | wall |
    |---|---|---|---|---|
    | shipped, 26 classes | **0.0580** | 0.1679 | 333 | 9.2s |
    | proportional, budget 333 | 0.2403 | 0.6640 | 543 | 28.4s |
    | proportional, budget 480 | **0.5396** | 0.6661 | 755 | 60.5s |
    **4x the error at a 63% larger pool, and 9x at a 127% larger one.**
    It gets monotonically WORSE as the budget grows, which rules out
    "not enough combos" as the explanation outright.
  - **Note the pool figure.** `combo_cap_fn` is applied to each side
    separately and the solve pool is the union, so a 333-per-side budget
    can produce anything from ~333 (heavy overlap) to ~666. M135's
    round-robin arm happened to land at 338 and so WAS matched against
    the shipped 333; this one is not, and is worse anyway.
  - **Seven rules, all worse than ranking classes by action purity:**
    mass-ranking, stratified-by-category, reserve-3, reserve-5,
    stratified-by-full-rank, round-robin combo budget, and proportional
    combo budget. They fail in both directions (0.0002 to 0.775) and
    include three that deliberately restore the diversity M130 said was
    missing. **The family is exhausted: no reweighting or resampling of
    a 169-class range into a ~330-combo budget beats the incumbent.**
  - **What that leaves.** Either more budget — the frontier is measured
    and M132 made the flop table 4.79x cheaper, so a wider cap is more
    affordable than it was — or the structural change M130 named, which
    is a different REPRESENTATION (bucketing strategically similar hands,
    M17's shelved machinery) rather than another way to choose among the
    169 classes. **Do not add an eighth scoring function.**

- **M137 — "more budget" is closed too, and cap 26 turns out to be an
  optimum rather than a compromise.** M136 exhausted the
  selection-rule family and left two paths: more budget, or a different
  representation. M132 had just made the flop table 4.79x cheaper, so
  the first looked newly affordable — the shipped setting now costs
  ~9.1s where M131's frontier priced it at 14.7s. Spending that on
  accuracy was the obvious next move. It does not work.
  - **Re-measured frontier, five spots against a full-range reference:**
    | arm | mean err | max err | wall |
    |---|---|---|---|
    | shipped: cap 26 / s30 / it500 | **0.0580** | **0.1679** | 9.1s |
    | cap 34 / s18 / it500 | 0.0568 | 0.2679 | 22.6s |
    | cap 34 / s18 / it800 | 0.0523 | 0.2160 | 32.4s |
    | cap 44 / s11 / it500 | 0.0551 | 0.2085 | 29.2s |
    Mean error improves by **at most 0.006 (10%) for 2.5-3.6x the
    cost**, and **worst-case error is WORSE at every wider cap** — 0.168
    shipped against 0.209-0.268.
  - **And it is not the precision being traded away.** Those arms scale
    samples down as the pool grows, so a flat result could equally mean
    "width helps, and the precision it cost was worth exactly as much" —
    especially since M130's sweep at a fixed 200 samples DID show width
    helping. Holding samples fixed at 30 and moving only the cap:
    | arm | mean err | max err | wall |
    |---|---|---|---|
    | cap 26, samples 30 | 0.0580 | 0.1679 | 13.8s |
    | cap 34, samples 30 | 0.0578 | 0.1575 | 29.0s |
    **0.0002 of mean error for 2.1x the cost.** Width genuinely stops
    paying past cap 26. (Within-run ratio, not the absolute seconds —
    cap 26 read 9.1s in the run above and 13.8s here; this machine
    drifts, which is why M70's rule exists.)
  - **So M131's cap 26 is an optimum, not a knee.** It was chosen as a
    cost/accuracy compromise; it turns out to have the best worst-case
    error of anything measured and to be within 0.006 of the best mean.
    Nothing in this dimension is being left on the table.
  - **Both cheap paths are now closed with measurements.** Seven
    selection rules are worse (M130-M136); more budget buys ~10% of mean
    error for 3x cost while making the worst case worse. **The mean
    error of 0.058 is the floor for this architecture at any shippable
    budget.**
  - **What remains is the one thing M130 named and nothing since has
    touched**: a different REPRESENTATION — bucketing strategically
    similar hands so a fixed budget describes the range's shape instead
    of sampling from it (M17's shelved machinery, whose goal there was
    speed and would be fidelity here). That is a real milestone, and it
    is now the only untried idea rather than one option among several.

- **M138 — bucketing is the ninth dead end, and finding that out
  exposed F36: the reference every accuracy claim since M130 was judged
  against had never converged.** M137 left exactly one untried path, a
  different REPRESENTATION. It loses. The measurement that showed it
  losing is what surfaced the real problem.
  - **Why M17's machinery could not be reused.** Both halves anchor on
    the full N x N combo table: `compute_combo_strengths` builds
    `build_board_equity_table` over the whole pool just to derive the
    bucketing signal, and `build_bucket_equity_table` averages over that
    same table. Bucketing all 169 classes needs a ~1,176 x 1,176 table —
    strictly more than the cap-26 solve that ships. M18's finding,
    re-confirmed by reading.
  - **The variant that avoids it**, built in scratchpad: cheap O(N)
    bucket assignment, then bucket-vs-bucket equity by SAMPLING member
    pairs — cost B^2 x k, independent of N. Dropped in at
    `query_strategy_from_path` so tree, pot, stack, positions and
    endpoint were production's and representation was the only variable.
    Two signals: the exact five-card rank, and — since rank is blind to
    draws — equity against a small random reference subset, made O(N x R)
    by ordering the reference first and passing `pair_rows`.
    | arm | mean err | max err | wall |
    |---|---|---|---|
    | **control: shipped cap 26** | **0.0580** | **0.1679** | 9.2s |
    | rank signal, 12 / 20 / 30 buckets | 0.1080 / 0.1095 / 0.1037 | 0.328-0.337 | 15-73s |
    | equity signal, 12 / 20 / 30 / 44 buckets | 0.1167 / 0.0852 / 0.1169 / 0.1268 | 0.269-0.347 | 19-78s |
    (errors against the OLD anchor, for comparability with M130-M137;
    against the corrected one, shipped is 0.1222 and bucketing ~0.20.)
  - **It fails by collapsing aggression specifically.** The three spots
    whose reference is ~0 are fine; the two that should bet come back
    ~0.02-0.11 at every setting, and at 12 and 20 rank-buckets the two
    return the IDENTICAL number — top set and an overpair in one bucket.
    Equal-mass bucketing lumps the strongest hands together because
    little mass lives up there, and averaging equity within a bucket
    destroys the spread value-betting monetises.
  - **Non-monotone in bucket count**, M136's signature. Equity-signal
    error runs 0.1167 / 0.0852 / 0.1169 / 0.1268 across B = 12/20/30/44.
    **Misread once during the measuring**: the first two points were
    taken as an improving trend and used to argue the signal mattered
    after all; the sweep withdrew that. Recorded because the dip at
    B=20 will fool the next person too.
  - **F36 — the reference was never converged.** Every accuracy number
    since M130 is an error against a cap-60 solve called a "full-range
    reference". It is 60 classes of 169, and it moves further than the
    differences it was used to adjudicate:
    | spot | cap 60 | cap 100 | cap 200 | cap 200 @ 2500 iters |
    |---|---|---|---|---|
    | 2h6d9c / 9s9d | 0.381 | 0.5948 | 0.9186 | **0.9870** |
    | 2h6d9c / QdQh | 0.2503 | 0.2842 | 0.0086 | **0.0014** |
    The uncapped solve is the trustworthy one: doubling equity samples
    moves 9s9d 0.9186 -> 0.9206 and the seed does not move it, while
    more iterations push it further the way widening already pointed.
    Both corrected answers are poker-sensible — top set on a dry board
    value-bets ~0.99, the overpair checks.
  - **Corrected error for the shipped config: mean 0.1222, worst
    0.4381**, against the 0.058 / 0.168 that was published. Reference is
    cap 200 / 200 samples / 2,500 iterations, ~850s per spot, which is
    why no earlier milestone ran it; still drifting slightly toward the
    extremes, so these are a LOWER bound.
    | spot | reference | shipped | error |
    |---|---|---|---|
    | 2h6d9c / 9s9d | 0.9870 | 0.5489 | 0.4381 |
    | 2h6d9c / QdQh | 0.0014 | 0.1501 | 0.1487 |
    | 2h6d9c / AhKh | 0.0001 | 0.0109 | 0.0108 |
    | Ac7d2h / 7s7c | 0.0010 | 0.0134 | 0.0124 |
    | Ac7d2h / KsQs | 0.0003 | 0.0014 | 0.0011 |
  - **The user-facing consequence, which is the point.** The caveat told
    players the raising frequency was off "by about 6 percentage points
    on average and by 17 at worst" — understating the worst case 2.6x in
    the one sentence a person actually reads. Corrected to 12 and 44.
    `POSTFLOP_AGGRESSION_ERROR_MEAN`/`_WORST` now hold the measurement
    and `test_the_aggression_caveat_quotes_its_own_measurement` pins the
    PROSE to them — pinning the constants alone would not have caught
    this, because prose and constants were wrong together.
  - **`api/config.py`'s "settling near 0.35" is withdrawn.** It read
    .301 / .336 / .347 at caps 34/44/60 and called that convergence.
    Three flat-looking points inside too narrow a window; past it the
    same spot climbs to 0.987. Same error as M110/M111's two-point
    trend, and as this milestone's own B=20 dip.
  - **M137's DECISION survives; its justification does not.** Re-run
    against the corrected reference, widening is still worse and still
    non-monotone — 0.1222 shipped against 0.1878 / 0.1912 / 0.1821 at
    caps 34 / 44 / 60 — so cap 26 stays. But "0.058 is this
    architecture's floor" was never a floor, it was a distance to a
    wrong target.
  - **Why eight attempts missed it:** all seven selection rules chose
    classes WITHIN a cap, so they shared the reference's blind spot and
    nothing disagreed. Bucketing broke the symmetry by seeing all 169
    classes; it looked like it was failing, and part of what it was
    doing was disagreeing with a wrong answer. **Any new accuracy claim
    on this axis must state its reference and show that it converged.**
  - No production behaviour changes: the shipped config is re-validated,
    not altered. What changes is what users are told and what the docs
    claim.

- **M139 — F37: the OTHER load-bearing reference was never converged
  either, and this one is quoted in three milestones' reasoning.** F36
  (M138) found the postflop accuracy reference had never been
  convergence-tested. The same question, asked of the project's other
  standing reference — the heads-up AA-jam figure of ~0.031 — gets the
  same answer.
  - **Measured.** Heads-up preflop, 100bb, exact solver (deterministic,
    so "converged" means stable in iterations), AA's open-jam at the
    root:
    | iterations | 500 | 1,000 | 3,000 | 12,000 | 30,000 | 60,000 |
    |---|---|---|---|---|---|---|
    | AA jam | 0.0159 | 0.0040 | 0.0004 | **0.0** | **0.0** | **0.0** |
    Monotone to zero. **The converged value is 0.0**; ~0.031 corresponds
    to roughly 300 iterations. It is also the poker-correct answer —
    open-jamming 100bb with AA heads-up is indefensible.
  - **What it touches.** M71 justified dropping the CFR+ clamp partly on
    plain CFR's 0.032 "matching" the reference; M97 rejected policy
    damping against it; M100 rejected the continuation knob against it.
    **None of those conclusions change**, because each rests on
    independent evidence — M71 on T7s's fold 0.744 -> 0.938 and on the
    ratchet mechanism read from the code, M97 on damping being worse on
    every arm, M100 on the sweep's non-monotonicity. What changes is a
    supporting claim in each: 0.032 is not a match, it is still 0.032
    above the right answer.
  - **One argument actually inverts.** M100 wrote that `c=1.0` "lands
    BELOW the ~0.031 reference" and read that as the knob gaming the
    target. Against the corrected reference of 0.0, 0.010 lands slightly
    ABOVE. The conclusion survives on the non-monotonicity; that
    particular sentence does not.
  - **Magnitude, stated honestly.** This is a 3-percentage-point error,
    where F36 was 60. It is recorded because it sits in reasoning three
    milestones lean on, not because it is large.
  - **A near-false-positive caught on the way, worth recording.** The
    same sweep showed heads-up AA at 10bb playing `call_or_check` 1.000
    while AKs jammed 1.000 — a categorical inversion, and a real defect
    in shipped advice if true. It is not true. Walking the continuation:
    SB limps AA, BB raises, **SB jams 1.000**; BB jams instead, **SB
    calls 1.000**. AA is limp-raising all-in — a trap, correctly played,
    with AKs jamming outright for fold equity. Checking the continuation
    before reporting is what separated this from a finding.
  - Docs only; no code change. The shipped solver is unaffected.

- **M140 — the postflop aggression defect has a named worst case, and it
  is worse than F36 measured.** F36 (M138) established the real error on
  five spots. Widening to sixteen across the strength ladder found the
  defect larger and, for the first time, a category a player can
  recognise.
  - **Open-ended straight draws are over-bet, 3 of 3**: +0.881 (7h8h),
    +0.411 (8h7d), +0.170 (7s8s). The worst recommends `raise:12.50` —
    ~2.5x pot — **0.88 of the time where the converged solve checks
    100%**. Verified three ways before reporting: byte-identical across
    three runs, reference converged at that spot (0.0004 / 0.0001 / 0.0
    at 1k / 2.5k / 5k iterations), and not a bet-size artifact.
  - **Incoherent within the hand class.** 7h8h / 8h7d / 7s8s are the same
    78 open-ender on a rainbow board with near-identical true
    frequencies (0.0001-0.0037), yet ship 0.8811 / 0.4142 / 0.1720 — 5x
    apart on suit composition alone, and ranked in the OPPOSITE order to
    the converged solve (7h8h holds the backdoor flush, is rated highest
    by the shipped config and lowest by the truth).
  - **Gutshots and the control are clean** (+0.0006, 0.0, 0.0), so the
    warning is scoped to OPEN-ENDED draws. Saying "draws" would
    over-generalise past the measurement — M110's error mirrored.
  - **Sixteen-spot totals: mean 0.1394, worst 0.8810**, against M138's
    five-spot 0.1222 / 0.4381 and the originally published 0.058 /
    0.168. Each widening of the spot set has made the defect look
    larger, so these are lower bounds — and the caveat is now on its
    third correction in two days.
  - **Direction, stated as measured rather than as it would be most
    useful.** Error tracks the TRUE frequency on all 16 spots: hands that
    should bet often are under-bet, hands that should rarely bet are
    over-bet. The stronger, more useful-sounding claim — "we under-bet
    your strongest hands" — is false across boards, since middle set
    flips sign between 2h6d9c (0.594 true, under-bet) and Ac7d2h (0.001
    true, over-bet). Only the open-ender case is both consistent and
    nameable by a player, so only it is stated as advice.
  - **Caveat rewritten and pinned.** It quotes 14 / 88, names the
    open-ender case, and tells players to discount those bets.
    `test_the_caveat_names_the_open_ender_case_it_measured` guards the
    category and the action; `test_the_aggression_caveat_quotes_its_own_
    measurement` keeps the prose tied to the constants. Both
    mutation-tested — **the first mutation attempt was a no-op**, because
    the caveat string is split across source lines and the edit searched
    for the concatenated runtime text, so the "passing" test proved
    nothing until the mutation was retargeted at the real source.
  - No solver change. What changes is what the user is told.

- **M141 — why nine ideas all failed: the cap MOVES error between hand
  types instead of reducing it.** M140 found the product's worst error is
  an open-ended straight draw at the shipped cap. Sweeping the cap with
  samples and iterations fixed, over the 16-spot set, shows why no
  setting has ever fixed anything.
  | group | cap 26 | cap 34 | cap 44 |
  |---|---|---|---|
  | made hands (sets/pairs) | **0.1052** | 0.2022 | 0.2458 |
  | draws | 0.2924 | **0.0031** | 0.0784 |
  | air / overcards | 0.0080 | 0.0018 | 0.0047 |
  - **Made-hand error grows monotonically with width; draw error
    collapses.** The two move in opposite directions, so mean error stays
    in a narrow band while its COMPOSITION shifts. Every one of the nine
    dead ends (M130-M138) was a single-knob reweighting of the same 169
    classes into a fixed budget — each could only redistribute error
    between hand types, never remove it. That is why nine structurally
    different rules all landed in 0.09-0.14: not a floor, a conservation
    law. **A tenth scoring function cannot help.**
  - **Cap 34 wins the raw 16-spot mean (0.0899 vs 0.1394) and is NOT
    adopted**, because the win is one spot deep:
    | test | cap 26 | cap 34 |
    |---|---|---|
    | mean, all 16 | 0.1394 | **0.0899** |
    | mean, excluding the worst draw | **0.0899** | 0.0953 |
    | worst case | **0.4381** | 0.7635 |
    | spots improved | — | 8 of 16 (6 worse) |
    | wall | **8.6s** | 23.0s |
    It also damages top set most (0.2235 against a true 0.987 — under-
    betting the strongest possible holding by three quarters), and
    getting top set wrong costs more than getting a draw wrong because
    with top set the money goes in. Draws are warned about instead
    (M140), not tuned for.
  - **A second methodological error, the same shape as F36 one level
    up.** The cap was chosen three times — M131, M137, M138 — on a
    five-spot set that contained NO DRAW, so the product's worst case was
    absent from the evidence that set the config. F36 was a reference
    that could not converge; this is a spot set that could not cover.
    Both let a conclusion look solid because the evidence was incapable
    of contradicting it. **Any future frontier here must cover made
    hands, draws and air explicitly**, since those are now known to move
    in opposite directions.
  - **Considered and rejected without trying:** selecting the cap from
    HERO's hand type (34 for draws, 26 otherwise). It would be tuning on
    the very 16 spots that produced the split, and it is incoherent on
    its face — the opponent's range does not depend on hero's cards.
  - What would actually help is a model whose approximation error is not
    paid by one hand type to spare another. Nothing in the
    class-selection family has that property.
  - No behaviour change. Config and docs only.

- **M142 — F38: the fold-versus-play call is not the sound half, and the
  caveat was telling users it was.** M141 closed the aggression axis. The
  caveat's remaining promise — "trust whether to continue" — had never
  been checked against a converged reference, only against M127's
  categorical play session. It does not hold.
  - **Why the promise survived so long:** every measurement behind it was
    taken at a street's OPENING decision, where **folding is not a legal
    action** — checking is free. The fold axis had effectively never been
    measured. Asking at a node facing a bet
    (`flop_action_path=["raise"]`), 10 spots, same converged reference:
    | axis | mean error | worst |
    |---|---|---|
    | fold / continue | **0.1870** | 0.8017 |
    | aggression | 0.1694 | 0.5573 |
  - **Strong hands are fine.** Top set, middle set and an open-ender all
    shove ~0.99 and the converged solve agrees; made hands and strong
    draws have fold error <= 0.011. The failure is entirely in WEAK hands
    facing a bet, 4 of 4: +0.170 (TsJs), +0.322 (AhKh), +0.561 (8s9s),
    +0.802 (KsQs).
  - **And it is not merely over-calling.** Holding nine-high on Ac7d2h
    the product recommends **all_in:97.50 at 0.5672 where the converged
    solve folds 0.9869**; KsQs raises 0.34 and shoves 0.06 with king-high
    on an ace board. A player following this loses a stack. Verified the
    same three ways as M140's outlier: byte-identical across runs,
    reference identical at 1,000 / 2,500 / 5,000 iterations, full action
    row inspected rather than one summed number.
  - **Why nothing caught it.** M127 judged 275 decisions categorically —
    premiums never folded, trash folded. This solver does fold air
    sometimes and never folds premiums, so it passes every such check
    while folding at a quarter of the correct rate; a frequency 4x wrong
    is invisible to a test that only asks whether a fold ever happened.
    Separately, M138-M141's 16-spot sweeps all measured the opening
    decision. **Any future postflop measurement must cover nodes facing a
    bet.**
  - **Caveat rewritten.** It no longer says the fold call is "far
    sounder" or to "trust whether to continue"; it names the case (weak
    hand, facing a bet), says what to do (fold more often, distrust any
    recommendation to commit chips), and still records where the axis IS
    exact (made hands and strong draws) so it does not over-claim in the
    other direction. `POSTFLOP_FOLD_ERROR_MEAN`/`_WORST` record the
    measurement. Two guards, both mutation-tested:
    `test_the_caveat_warns_about_weak_hands_facing_a_bet` and the
    corrected `test_the_aggression_caveat_does_not_claim_the_fold_call_
    is_broken`, whose original premise ("only the aggression axis was
    measured unstable") this milestone falsifies.
  - No solver change. What changes is what the user is told — and this is
    the first correction in the series that changes an INSTRUCTION rather
    than a number.

- **M143 - F39: the affordability guarantee was broken on every turn and
  river node.** Screening streets and node types after F38 turned up
  something objective rather than statistical: advice naming a bet the
  same response says the player cannot make.
  - **Measured**, production settings, `/advise`: **8 of 40 nodes
    violate, and all 8 are turn-facing-a-bet** - every board, every hero
    hand. Each reports `max_affordable_bb: 85.0` beside `all_in:97.50`.
    The arithmetic: hero has 97.5 behind entering the turn, villain bets
    12.5, and the bound was reported as the 85.0 left after calling
    rather than the 97.5 hero can actually commit.
  - **Cause.** M101 introduced `max_affordable_bb` precisely because
    `effective_stack_bb` means different things by node and shares no
    baseline with action sizes - and then set it on preflop and flop
    responses only. **All eleven turn/river responses omitted the
    field**, so `api/main.py` filled it from its
    `raw.get("max_affordable_bb", raw["effective_stack_bb"])` default:
    the exact quantity M101's own comment says cannot be compared to a
    size.
  - **Fix.** Each response now carries the stack entering ITS OWN street,
    captured before that street's betting reduces it - `turn_entry_stack`
    in `_query_turn_from_path` and `_query_turn_multiway_from_path`,
    `river_entry_stack` in `_query_river_from_path`. Sweep goes 8
    violations -> 0; the turn node now reports `max_affordable_bb: 97.5`
    against `effective_stack_bb: 85.0`, so the two still diverge and the
    bound is not vacuous.
  - **Why M101's sweep could never have caught it, even extended.** The
    autouse fixture sets `FLOP_TURN_RAISE_SIZES = ()` for speed, so under
    the suite the turn tree offers only check and all-in and **no
    mid-street turn node is reachable**. Adding turn cases to the
    parametrized sweep simply 422s. The new guard restores one real size
    (`(2.5,)`, `FLOP_TURN_MAX_RAISES = 2`) so the node exists, and is
    mutation-tested: reverting the fix fails it with
    `assert not ['all_in:97.50']`.
  - **Fourth instance of one meta-pattern**, now the dominant theme of
    this stretch: F36 a reference that could not converge, M141 a spot
    set with no draws, F38 a node type where folding was not legal, F39 a
    node type the fixture made unreachable. Every time, a guarantee held
    everywhere it was checked and the checking had a shape-shaped hole.
    **A test fixture that shrinks the tree can delete the very node a
    guarantee is about.**
  - **Recorded and deliberately not "fixed":** after a checked-through
    flop and turn, a river node's only legal actions are check and
    all-in, so `river_action_path=["raise"]` returns 422. Honest
    behaviour and a tree-shape limitation rather than a defect, but it
    means a river decision facing a normal bet cannot be expressed
    through that line.
  - First real code fix of this stretch - everything since M138 changed
    only what users are told. 973 backend tests pass.

- **M144 - F40: the river models no bet size except all-in, and nothing
  said so.** Fixing F39 surfaced a river node whose only actions were
  `call_or_check` and `all_in:97.50`. That was recorded as a tree-shape
  limitation; checking the config showed it is the shipped state of the
  whole street.
  - **`FLOP_TO_RIVER_RAISE_SIZES = ()`.** Measured through `/advise` at
    production settings, the sizes actually offered at each street's
    opening decision:
    | street | actions |
    |---|---|
    | flop | call_or_check, raise:12.50, all_in |
    | turn | call_or_check, raise:12.50, all_in |
    | river | **call_or_check, all_in** |
    The surrounding comment justifies the differing `max_raises` on cost
    grounds; **the empty river SIZES were never separately justified.**
  - **Why it matters to a user, not just a reader of config.** A player
    asking how much to bet the river cannot be answered at all. Worse,
    `all_in: 0.11` looks like a considered judgement that shoving beat
    betting smaller - but smaller was never a legal action in the tree,
    so nothing was compared and nothing rejected.
  - **Surfaced, not silently "fixed".** Widening the river tree is
    exactly the cost `solve_flop_to_river`'s own budget notes say it
    cannot afford (its DEFAULT 20 iterations already costs ~63-105s), and
    inventing a size without measuring it would be worse than stating
    what was modelled. Every response now reports
    **`modelled_bet_sizes`**, and `BET_SIZING_COVERAGE_NOTE` is appended
    to the aggression caveat when all-in is the only way to commit chips.
  - **Both signals are derived from the response's own strategy rows,
    never from the config constants.** Reading the constants would make
    the disclosure a second thing to keep in sync - the failure mode F36
    and M143 both came from. Guarded two ways: the river must report
    all-in only AND carry the note, and an earlier street must NOT carry
    it, so the note cannot degrade into blanket postflop noise.
    Mutation-tested by suppressing the append.
  - 975 backend + 157 frontend tests pass.

- **M145 - F41: a node where nothing was trained was served with
  `solver_confidence: "high"`.** Screening multiway postflop - the
  thinnest-covered surface, after F38/F39/F40 all turned up on
  under-guarded ones - found the honesty signals contradicting each
  other.
  - **Measured.** A 3-max river (`Kd7c2h` / `Ts` / `4c`): **0 of 136
    hands trained, 137 of 137 rows exactly uniform.** Hero's advice reads
    `call_or_check 0.3333 / raise:18.75 0.3333 / all_in:97.50 0.3333`.
    The response reported `solver_confidence: "high"` and
    `range_confidence: fully_trained: true` for BTN, SB and BB. Only
    `hero.trained: false` told the truth, one field among many.
  - **Occasional, not systematic** - 1 of 6 measured cases (two table
    sizes, four boards); the others trained 46-50 of ~130. That is worse
    for a user than a consistent gap: most requests look fine and nothing
    on the response distinguishes the one that is not.
  - **A uniform split is the perfect disguise.** `0.3333 / 0.3333 /
    0.3333` reads as a legitimate mixed strategy - genuinely indifferent
    between three actions - when it actually means "never computed". The
    new reason says so explicitly: an even split here means "no answer",
    not "genuinely indifferent".
  - **`range_confidence` misleads without being wrong.** It reports the
    PREFLOP range derivation, and those nine classes per position really
    were fully trained. Composed with a river strategy that was never
    solved, it functions as an endorsement of something it says nothing
    about.
  - **Fix.** `solver_confidence` was a pure function of table size; it
    now also consults `_node_is_untrained`, and reports BOTH reasons when
    a low-confidence table size applies as well - they are different
    problems and a user acting on one should still see the other.
  - **Deliberately scoped to the unambiguous case.** A single hand
    reading untrained is often benign: `trained_hands` documents that a
    hand with zero reach in this position's range is untrained at any
    iteration count. Flagging that would fire constantly and make "low"
    meaningless. Zero trained hands at the WHOLE node cannot be benign -
    it means the node was never visited. Pinned by a test asserting the
    signal does NOT fire on one untrained hand, on an empty `trained`
    dict, or on an absent one.
  - **A near-miss in the measuring, recorded.** The first mutation test
    reported the guard surviving. It had not: `-k "untrained"` never
    selected `test_a_node_nothing_was_trained_at_...`, whose name
    contains `was_trained`. Re-run with explicit node ids, both guards
    fail under mutation. A mutation test that selects the wrong tests
    proves nothing, and prints the same reassuring output.
  - 978 backend tests pass.

- **M146 - F42: a cached branch was not necessarily a trained one, which
  is the root cause of F41.** M145 disclosed that a node could be served
  wholly untrained; this is why it happened.
  - **The bug is an ordering one.** `ensure_mccfr_chance_branch` began
    with an unconditional `if key in result.chance_data: return`, and M75
    added its `train_iterations` block BELOW it. Any branch already in
    `chance_data` was returned with whatever training it had.
  - **Branches come to exist untrained in normal operation.** While one
    branch is trained, its own `chance_fn` dispatches into the next
    street's branches and stores them in the same `result.chance_data`
    (it is passed in as `chance_data=`). Those entries get a root and an
    all-zero `InfoSetTable` and nothing else.
  - **So sampling a card is what poisons it** - the opposite of what
    intuition suggests. Traced directly:
    | river card | already cached | node_data added | trained |
    |---|---|---|---|
    | 4c | **yes** | 0 | 0/136 |
    | 9h | no | 41 | 46/136 |
    After the fix, 4c trains at **50/136**.
  - **The first fix was a no-op, and the re-run caught it.** It tested
    `id(cached.root) in result.node_data` as a proxy for "trained". The
    dispatch creates that entry too - just empty - so the condition was
    always true and nothing changed. The real question is whether the
    entry ACCUMULATED anything: `trained_mask()` is exactly the
    `totals > 0` condition `average_strategy()` already checks before
    substituting the uniform prior, which is also the question the
    honesty signal asks, so fix and disclosure now agree by construction.
  - **The first regression test was worthless, and the mutation caught
    that.** It hunted for an untrained branch the dispatch happened to
    create, and under mutation it SKIPPED rather than failed - reverting
    the fix also removed the dispatch that produced its own precondition.
    Rewritten to build the condition directly (`train_iterations=0`, then
    ask again with training), it fails correctly and prints the all-zero
    table. It also pins the other direction: a second ask must NOT
    retrain, or every request would pay for training already done.
  - **Cost: none measurable.** 27.4s cold for the previously-untrained
    case against 27.0s for one that always trained; warm 0.005s.
  - F41's disclosure stays: it is the safety net for any node that is
    still untrained for a different reason. 979 backend tests pass.

- **M147 - multiway turn/river facing a bet: screened, nothing wrong.**
  The last node type no guard reached. Six consecutive findings came from
  exactly this shape of gap (F36-F42), so the negative result is worth
  recording as much as the positives were.
  - **8 nodes** - 3-max and 6-max, turn and river, facing a bet, two
    boards. Every one clean: no size above `max_affordable_bb`, every
    node trained (47-64 hands of ~130-174), no untrained rows, and no
    stack committed with a genuinely weak hand.
  - **It confirmed two things that had been taken on trust.** M143 fixed
    `_query_turn_multiway_from_path` alongside the heads-up path but only
    swept heads-up afterwards, so the multiway half shipped on a reading
    of the code - the exact habit this stretch kept punishing. And F42's
    fix holds on a second path: no untrained node appeared anywhere here.
  - **A near-false-positive, the third of its kind this session.** The
    6-max river on Kd7c2h/Ts/4c flagged hero committing 0.21 with what
    the harness labelled "no pair, no draw" - but the river 4c pairs
    hero's 4d. Re-run with genuine air (9s6h, Jh8d): 0.152 and 0.013,
    both below threshold, and the pair correctly commits MORE than the
    air hands. Hand labelling has now nearly produced a false finding
    three times (8s9s in M140, this, and the M142 screen); check the hand
    category, not the hole cards.
  - **The real gap was in the testing, not the product.** The multiway
    affordability fix had no test and could not have one under the
    fixture: `_disable_prewarm_and_clear_cache` sets
    `MULTIWAY_FLOP_RAISE_SIZES = ()`, so the mid-street multiway turn
    node does not exist in the suite - the same fixture-shaped hole as
    F39. `test_the_affordability_bound_survives_a_multiway_turn_facing_a_
    bet` restores the sizes so the node exists.
  - **Mutation-testing it needed care.** The first attempt reverted only
    the heads-up site (the multiway one differs in indentation) and the
    multiway test passed anyway - which would have left the guard
    believed-good and unproven. Targeting the multiway live-decision
    response specifically, it fails correctly.
  - 980 backend tests pass.

- **M148 - the frontend was showing the number M101 exists to replace.**
  The advice header rendered `pot X / {effective_stack_bb}bb effective`
  **directly above** a strategy row quoting sizes on a different baseline,
  so a real turn node displayed "85bb effective" above `all_in:97.50`.
  That is F39's confusion surfaced to users rather than to API consumers.
  - **Fixed** to show `max_affordable_bb` - the one bound every size in
    the strategy can be compared against - as "up to N bb".
  - **F40's river gap now reaches the UI** as its own hint rather than
    only inside the 245-word caveat paragraph: "Only checking/calling and
    going all-in were modelled here - no smaller bet size was available
    to the solver." Derived from `modelled_bet_sizes`, not from the
    street, so it stays true if the sizing constants move.
  - **Both new response fields are now typed.** They were absent from
    `AdviseResponse`, and the test fixture is typed
    `Partial<Record<string, unknown>>`, so **`tsc` could not catch it** -
    the existing tests had been rendering `up to undefined bb` and
    passing, because nothing asserted on it.
  - **A wrong-framework false start, recorded.** The first tests used MSW
    (`server.use` / `http.post`); this suite drives the component with
    `vi.stubGlobal('fetch', mockFetch(...))`. Rewritten to match. A
    matcher also had to be narrowed - both the preflop WALK line and the
    advice line contain "to act, pot", and the first version asserted
    against the walk line.
  - **Mutation-tested**: reverting both UI changes fails 2 tests.
  - `onlyAllInModelled` lives in `frontend/src/betSizing.ts`, not in the
    component file - the linter flags a non-component export as breaking
    fast refresh, and `hands.ts` / `colors.ts` set the precedent.
  - **The caveat length is left alone, deliberately.** Measured at 245
    words / 1,424 characters, which is long for a warning panel. Every
    sentence is a measurement, and the two actionable lines (discount
    open-ended-draw bets; fold weak hands facing a bet) would be the
    first casualties of compression. Splitting it presentationally means
    sentence-parsing text containing figures like "97.5bb", and
    collapsing it hides content inside a `role="alert"`. Shortening it is
    a decision about which measurement to drop, not a mechanical fix.
  - 980 backend + 161 frontend tests pass.

- **M149 - F43: `trained` means VISITED, not LEARNED, and the gap is
  user-visible on the most expensive decision in preflop poker.** Found
  while scoping the M112-M116 continuation fix, and it is the same root
  cause as that fix's blocker.
  - **Measured through /advise.** 6-max, hero AA facing a 4-bet:
    | field | value |
    |---|---|
    | hero strategy | fold 0.3333 / call 0.3333 / all-in 0.3333 |
    | `hero.trained` | **true** |
    | `solver_confidence` | **high** |
    | node trained | 101/169 |
    Folding aces to a 4-bet a third of the time is a stack-losing
    instruction, and every signal called it fine.
  - **Why F41's guard correctly stayed quiet.** M145 flags a node where
    NOTHING is trained; here most of the node is. `trained_mask()` asks
    whether a hand accumulated any strategy_sum - whether it was VISITED.
    `current_strategy()` returns the uniform prior whenever every regret
    is <= 0, and M73 measured ~70% of rows all-negative. So a hand can be
    visited repeatedly and still average to exactly the prior. The
    distinction was documented in the solver and never connected to the
    honesty signals.
  - **Heads-up is unaffected**, measured at the same spots: BTN opens
    0.998, facing a 3-bet 0.48/0.52, facing a 4-bet jams 1.0. This is the
    sampled multiway solver failing to reach deep preflop nodes, not a
    property of the signals.
  - **Scoped three ways** so the signal keeps meaning something: EXACT
    uniformity only (near-uniform is a real answer sitting near
    indifference); only when `trained` is true (a false `trained` already
    fires a louder hero-specific warning, and saying it twice reads as
    two problems); never on single-action rows or absent data.
  - **The wording earns its length.** An even three-way split is a
    legitimate solver output, so the reason says explicitly that this one
    is "not a recommendation to mix evenly" - without that a player could
    reasonably act on it.
  - **Shipped as a no-op first, caught by the live sweep.** The first
    version read `raw.get("hero")`, but `hero` is assembled inside
    `advise` and never lands in `raw`, so it returned False for every
    real request while every unit test passed - they fed it a hand-built
    dict shaped the way the response was ASSUMED to look. Second time
    this session (M146's first fix had the same shape). There is now a
    deliberate wiring test that stubs only the solve and exercises the
    real hero assembly and response shaping; mutation-testing confirms
    reverting ONLY the wiring fails that test alone, while both unit
    tests still pass.

- **M150 - deep multiway preflop nodes are solved on demand.** The
  architectural fix M112-M116 was reaching for, arrived at from the other
  end: instead of correcting terminal PRICING with continuation values,
  fix the unlearned NODES that made those values unbuildable (M149).
  - **The measurement that reframed it.** The 6-max preflop tree has
    **289,036 decision nodes**, and the production cached solve learns
    roughly the first four levels:
    | depth | 0-2 | 3 | 4 | 5 | 6 | 7 | 8+ |
    |---|---|---|---|---|---|---|---|
    | nodes | 19 | 46 | 145 | 441 | 1,118 | 2,678 | ~285,000 |
    | learned | 100% | 80% | 48% | 21% | 12% | 3% | **0%** |
    Neither instinctive fix survives contact with that: 285,000 nodes
    cannot be targeted-trained, and M72/M73 measured 6-max destabilising
    at 12k iterations - orders of magnitude short of covering depth 8.
  - **The pattern was already in the codebase, one street later.**
    `ensure_mccfr_chance_branch` (M75, fixed M146) builds and trains a
    postflop branch when a client actually asks for it. The preflop tree
    had the identical problem and no equivalent. **A deep subtree is
    small for the same reason it is deep** - the F43 node (depth 6) has
    **10 nodes** below it.
  - **Measured through /advise:**
    | spot | before | after |
    |---|---|---|
    | 6-max AA facing a 4-bet | 0.3333 / 0.3333 / 0.3333 | **jam 0.9999** |
    | 6-max trash facing a 4-bet | 0.3333 / 0.3333 / 0.3333 | **fold 0.998** |
    | 6-max AA facing an open | already correct | unchanged |
    | HU AA facing a 4-bet | jam 1.0 | unchanged |
    The trash row is repaired for free: one solve trains every hand at
    the node (101/169 -> 169/169). 2.2s of work at 200 iterations, then
    cached - warm 0.07s.
  - **The reach is uniform, and that is an assumption rather than a
    derivation.** The ranges reaching a deep node are precisely what is
    not known, because its parents are unlearned too (M149). This
    converts "never computed" into "computed against a stated prior" -
    an improvement in kind, not a complete answer, and the docstring says
    so rather than implying the subtree solve is unconditionally right.
  - **Scoped**: fires only when hero's own row IS the prior or nothing at
    the node is trained, re-checks under the cache lock so concurrent
    requests do not both pay, and excludes heads-up outright (its exact
    solver enumerates every hand at every node).
  - **Three measurement bugs of my own surfaced getting here**, all
    caught by cross-checking rather than by the numbers looking wrong: a
    missing `__main__` guard let pool workers print junk (31 decision
    nodes); a node counter dropped its root reference so garbage-collected
    subtrees had their `id()`s reused (30 nodes, against a verified
    289,036); and the first coverage probes called `solve_preflop`
    directly with default seed and hands, measuring a solve production
    never runs - `_get_or_solve_multiway` passes
    `hands=MULTIWAY_PREFLOP_HANDS`, an equity cache, `floor_regret` and
    **seed=1**, and M110 documented 6-max varying enormously by seed. The
    coverage table above is re-measured on the production solve.
  - F43's disclosure (M149) stays as the safety net for any node still
    returning the prior for another reason.

- **M151 - what the river's missing bet sizes actually cost.** F40 (M144)
  framed the gap as "cannot tell you how much to bet" and surfaced it.
  Re-solving the same river spot with one normal size available shows it
  changes the ACTION, in both directions.
  - **Measured**, `Kd7c2hTs4c`, pot 5 / stack 97.5, cap 26, standalone
    river solve at 0.75x pot:
    | hero | shipped check | with a size | shipped all-in |
    |---|---|---|---|
    | AsKs top pair | 0.9941 | 0.6449 (bets 0.35) | 0.0059 |
    | KhQd top pair | 0.9941 | 0.5206 (bets 0.48) | 0.0059 |
    | **9s8s nine-high** | 0.0121 | 0.0048 (bets 0.98) | **~0.988** |
    | 7d7h middle pair | 0.7055 | 0.9511 | 0.2945 |
    | AcQc ace-high | 1.0000 | 0.9982 | 0.0000 |
    With all-in the only way to bet, the strategy collapses into
    check-or-shove: **value hands check when they should bet small, and
    bluffs move a whole stack into a 5bb pot when they should bet 3.75.**
  - **The bluff half is F38 arriving by a different route.** There, weak
    hands over-committed because of RANGE composition; here it is the
    SIZE menu. Two mechanisms, one user-visible failure - fixing either
    will not fix the other.
  - **Cost of the two possible fixes, both measured.**
    `solve_flop_to_river` takes ONE `raise_sizes` for all three streets,
    so enabling river sizes widens flop and turn as well, and that
    chain's DEFAULT 20 iterations already costs 63-105s. A standalone
    river solve is cheap - **~7s**, and river equity is EXACT rather than
    sampled because the board is complete - but uses ranges that skip
    flop/turn narrowing.
  - **Not fixed, deliberately.** A first reading suggested checked-through
    lines would be exempt from the range approximation, since nobody bet.
    That is wrong: checking is itself an action with frequencies in a
    solved strategy, so even a checked-through line carries information.
    The approximation is real in every line, and trading a disclosed gap
    for an unvalidated model is not an improvement - the same reasoning
    that declined cap 34 in M141 despite it winning the headline metric.
  - **The disclosure now says what was measured.** It previously said the
    response could not tell you how much to bet; a player reading only
    that would keep checking down value hands. It now says the missing
    size distorts the play in both directions, and
    `test_the_river_says_it_modelled_no_bet_sizes` pins both halves.
  - 987 backend tests pass.

- **M152 - precision is dead too, and the metric that made it look alive
  is biased.** Reviewing how the postflop defect had been measured
  surfaced an axis nobody had isolated: M137 held samples at 30 and moved
  the CAP; M138's converged reference moved cap, samples and iterations
  TOGETHER. So "the cap is what binds" was never tested against its
  complement.
  - **Cap held at 26, precision raised**, against M138's converged
    references:
    | arm | mean err | worst | wall |
    |---|---|---|---|
    | shipped s30/it500 | 0.2992 | 0.881 | 9.7s |
    | s200/it500 | 0.1840 | 0.4764 | 13.5s |
    | s30/it2500 | **0.1268** | 0.4762 | 36.4s |
    | s200/it2500 | 0.1752 | 0.588 | 40.2s |
    Non-monotone: both knobs together is WORSE than iterations alone.
  - **Per spot it is M141's conservation law on an independent axis.**
    More precision pushes everything toward betting less:
    | hero | true | shipped | s30/it2500 | |
    |---|---|---|---|---|
    | 9s9d top set | 0.9870 | 0.5489 | 0.5108 | **worse** |
    | 6s6c middle set | 0.5940 | 0.5784 | 0.4958 | **worse** |
    | QdQh overpair | 0.0014 | 0.1501 | 0.0060 | better |
    | 7h8h open-ender | 0.0001 | 0.8811 | 0.0547 | better |
    | 7s7c middle set | 0.0010 | 0.0134 | 0.0005 | better |
    3 of 5 better, 2 of 5 worse - cap 34's signature. **Nothing yet moves
    top set toward its true 0.987.**
  - **The metric problem is the bigger find.** Three of five spots have a
    true value near ZERO, so any change reducing aggression improves the
    mean without improving the advice. That is why iterations appear to
    halve the error, and why cap 34 won M141's raw 16-spot mean. **Mean
    error over an unbalanced spot set is not a usable metric for this
    defect**; per-hand-type error is.
  - **No config change.** it2500's 3.75x cost buys a mean that is an
    artifact while making the spots a player most wants right worse.
  - **Also repairs a lost doc.** M141's CLAUDE.md block never landed: its
    apply script wrote `api/config.py` first, then hit a failed assertion
    on CLAUDE.md, and the traceback was masked because that command timed
    out and was moved to the background. The commit shows only config.py
    and milestones.md. CLAUDE.md is the file loaded every session, so the
    conservation law was missing from exactly where it matters. Restored
    here alongside M152.

- **M153 - F44: `equity_seed` was silently dropped, and one converged
  reference turns out to be a coin flip.** Chasing whether a cheap
  full-range solve could replace the shipped cap turned up both.
  - **The cheap-full-range idea is dead on cost.** At cap 200, s30 costs
    **827s** against the reference's ~850s at s200 - cutting samples
    saved nothing, because the ~1.38M-pair equity table over ~1,176
    combos dominates, not the per-pair sample count. Offline
    precomputation over 1,755 canonical flops would be ~400 hours.
  - **It did confirm the diagnosis**: the full range is what fixes value
    hands. Top set reads 0.549 at cap 26 and **0.9887 at cap 200**,
    matching the reference to 0.0017. The cure is correct and
    unaffordable, which is more useful to know than another failed
    selection rule.
  - **F44.** `parallel_board_equity_table(board, combos, samples=None)`
    took no seed and hardcoded `DEFAULT_SEED`; `solve_flop` called it as
    `equity_table_fn(board, combos, equity_samples)`. So from M132
    onward, `equity_seed` did nothing whenever a builder was injected -
    the production path. The tables were never wrong, but **a
    seed-variation convergence check on that path could not vary
    anything**, and M138 used exactly that check as evidence. Found
    because seed pairs kept coming back byte-identical; an innocent
    explanation was nearly accepted for the second time before the call
    site was read.
  - **Reference stability, re-measured** at cap 200 / it2500:
    | spot | s30 | s60 | s60 seed99 | s100 | s200 (ref) |
    |---|---|---|---|---|---|
    | 9s9d top set | 0.9887 | 0.9853 | 0.9853 | 0.9889 | 0.9870 |
    | QdQh overpair | 0.0013 | **0.8614** | **0.8614** | 0.0014 | 0.0014 |
    9s9d is stable across every cell. QdQh flips wholesale at s60,
    reproducibly - **M74's bang-bang behaviour** on a near-tied decision.
    Its reference is one side of a coin flip, so per-spot errors against
    it, including the 0.1487 that fed M138's headline mean, are not
    meaningful. The load-bearing findings do not rest on it.
  - **Left open, deliberately**: `build_chance_node` accepts neither
    `equity_samples` nor `equity_seed`, so turn/river chance-branch
    tables always use library defaults. Whether that is deliberate is not
    established, and guessing would be the error this milestone exists to
    correct.

- **M154 - the M153 open item resolves as a non-defect, and is now
  pinned.** M153 flagged that `build_chance_node` accepts neither
  `equity_samples` nor `equity_seed`, and deliberately did not guess
  whether that was intentional.
  - **It is intentional and correct.** `solve_flop_turn`'s docstring says
    its tables are "resolved exactly, not sampled, so there's nothing to
    tune". Measured rather than taken on trust:
    | board | samples/rng ignored |
    |---|---|
    | flop (2 to come) | **no** - sampled |
    | turn (1 to come) | **yes** - exact |
    | river (0 to come) | **yes** - exact |
    `remaining_needed <= 1` enumerates every single-card runout. Every
    table a chance node builds is a turn or river board, so no sample
    count could apply.
  - **A near-miss worth recording.** `build_board_equity_table`'s opening
    summary lists what it averages - "2 for a flop board, 1 for a turn
    board" - which reads as though turn boards are sampled, and looked
    like a contradiction with `solve_flop_turn`. The detailed paragraph
    below it states the `remaining_needed <= 1` exception correctly. A
    docstring's summary and its detail disagreeing in appearance is
    enough to send someone chasing a defect that is not there, which is
    why the property is now a test rather than a comment.
  - Guarded in both directions: the flop table MUST still depend on its
    sample count, and turn/river tables must not. If turn boards ever
    became sampled, the chance-node path would silently use library
    defaults for a quantity its callers think they control.

- **M155 - a 120-hand play session, and the cost structure that explains
  its latency.** Same hand count and seed as M127's session so the two
  compare, with a check added for every defect found since.
  - **Correctness: 287 decisions, zero defects**, against five checks
    that did not exist before - uniform-prior rows (F43), untrained nodes
    (F41/F42), unaffordable bets (F39), undisclosed all-in-only streets
    (F40/M151), and stack commitments with weak hands (F38). Four of the
    five exist because a real failure got past M127's categorical checks,
    which passed 275 decisions while the solver folded at a quarter of
    the correct rate.
    | check | hits | undisclosed |
    |---|---|---|
    | answer is the uniform prior | 0 | 0 |
    | nothing trained at the node | 0 | 0 |
    | bet above `max_affordable_bb` | 0 | 0 |
    | all-in is the only modelled size | 56 | **0** |
    | stack committed with a weak hand | 19 | n/a |
  - **Latency is the regression**: p50 4.28s -> 5.62s, p90 8.71s ->
    **35.79s**, p99 79.3s, worst 87.4s, 157 of 287 decisions over 5s.
    Part was bought deliberately - M131 widened the cap 10 -> 26 knowing
    it took a flop decision from 8.4s to 14.7s - but that benchmark used
    ONE fixed board. Across random boards the same path runs 16.8s median
    and 64.5s at p90, and cost tracks stack depth (5.4s at 20bb against
    17.5s at 50bb).
  - **Where the time goes, measured rather than assumed.** The equity
    table is **14%** of the solve leg, not M132's stale 41% - that
    milestone's own speedup shrank it. The CFR solve is 86%. End to end
    through `/advise`, `query_strategy_from_path` is 5.2-16.6s and the
    cached preflop solve ~3.5s, with nothing unaccounted.
  - **Hero's force-inclusion is inside the solver's own noise.** Adding
    hero moves other hands by p90 0.002-0.107; a seed-only re-run of the
    identical hero-free solve moves them **as much or more** (p90
    0.024-0.112). So the per-hero cache partition costs a full cold solve
    - on 71 of 73 flop requests - to preserve noise. This is the M124
    control applied one street later, and it was only askable because
    M153 fixed the dropped `equity_seed`.
  - **Not yet fixed, and why.** Serving an out-of-range hero from one
    cached canonical solve still needs hero's own row. Best response
    against the cached villain strategy is the principled option and
    gives PURE advice, losing the mixing every other row has; a
    warm-started refinement keeps mixing but needs `node_data` keyed by
    something stable across rebuilt trees, since it is keyed by
    `id(node)` today. Both are real milestones, and shipping either
    half-validated would repeat the M151 trade this project already
    declined.
  - Unchanged and confirmed: 19 weak-hand stack commitments (F38,
    disclosed not fixed) and 9-max T7s folding 0.152 under the gun
    against a documented 0.125 - fully trained and non-uniform, so
    M150's on-demand solving does not touch it.

- **M156 - F38's worst cases are a tree-shape defect, not a range defect.**
  Item 2 of the M155 work list. The play session recorded 19 stack
  commitments with weak hands facing a bet; the severe ones are on the
  turn and river.
  - **Cause, measured.** Facing a bet the turn offers exactly `fold /
    call_or_check / all_in:97.50` - no sized raise at all.
    `FLOP_TURN_RAISE_SIZES=(2.5,)` with `FLOP_TURN_MAX_RAISES=2` provides
    one size for the FIRST raise, so a re-raise can only be a shove. The
    flop is unaffected: `(2.5, 3.0, 2.2)` at max_raises 4 gives
    `raise:37.50` facing a bet. **A hand that wants to raise a third of
    the pot has no such action, so the weight lands on the only
    aggressive button that exists.**
  - **The fix works on the worst case and costs elsewhere:**
    | hand | shipped | sized re-raise |
    |---|---|---|
    | middle pair | shove **1.000** | sized 0.576, shove 0.424 |
    | top pair | call 0.866, shove 0.134 | sized 0.348, shove **0.475** |
    | open-ender | fold 0.996 | fold 0.996 |
    | wall | 18-20s | 28-32s |
  - **Not adopted, deliberately.** Middle pair stacking off 100% facing
    one bet is indefensible and the sized raise more than halves it - but
    top pair's shove more than triples, which is M141's conservation
    pattern appearing on a third axis (cap width, precision, and now the
    size menu). Adjudicating needs a converged TURN reference, which
    costs what a flop reference costs. M155 measured latency as the top
    user-facing problem, so paying 1.5x on the turn leg for an unmeasured
    accuracy change is exactly the trade M151 declined.
  - **The disclosure already covers it**, verified rather than assumed: a
    turn-facing-a-bet node reports `modelled_bet_sizes: [97.5]` and
    carries the note that the missing size distorts the play in both
    directions. The 56 all-in-only nodes in the M155 session were all
    disclosed.
  - **What would change the verdict**: a converged turn reference, or a
    way to add the action without the 1.5x - the tree grows because
    `max_raises` deepens every line, not just the one facing a bet.

- **M157 - 9-max was left at a budget its own config called
  insufficient.** Item 4 of the M155 work list, and the claim blocking it
  turned out to be an inference.
  - **What config.py said**: 9-max "does not converge at any affordable
    budget", keeping 3,000 iterations "because more is directionally
    better, not because it is enough". The 12.5% T7s figure behind that
    is real - measured at ONE budget. The conclusion that a converging
    count is unaffordable came from per-iteration cost arithmetic, and no
    higher budget was ever run.
  - **Measured, three seeds, no overlap between arms:**
    | arm | T7s fold | mean | AA jam | 72o fold | wall |
    |---|---|---|---|---|---|
    | 3,000 + clamp | .1522 / .0678 / .1450 | 0.122 | .81-.85 | .973-.982 | 139-168s |
    | **12,000 plain** | **.8628 / .4508 / .8783** | **0.731** | **.06-.17** | **1.0000** | 473-580s |
    Every seed of the new arm beats every seed of the old on T7s (min
    0.4508 against max 0.1522). 6-max's documented figure is 0.874.
  - **It also satisfies M71's own condition.** M71 kept the CFR+ clamp at
    9-max explicitly "until its budget can support the better rule",
    because plain CFR went the wrong way at 333 traversals per seat. At
    12,000 that is 1,333 per seat and plain CFR wins on every measure -
    and the effects compound, since M71 established the clamp is a
    ratchet that worsens with iterations.
  - **The warning stays, rewritten.** The seed spread on T7s is 0.43, so
    9-max is materially better advice rather than a converged solve. The
    old text quoted "reaches only 0.30 at 9,000 iterations" and "does not
    converge at any affordable budget"; both are now false and both are
    gone. Guarded by a test that asserts the shipped budget and the
    absence of the withdrawn numbers.
  - **A fixture wrinkle worth knowing**: `_disable_prewarm_and_clear_cache`
    rewrites every table's iteration count down for speed, so a test
    asserting the SHIPPED budget has to parse it out of the config source
    rather than read the patched module.
  - Cost is 3.1x on a solve cached per (stack, players) and pre-warmed at
    startup. 993 backend tests pass.

- **M158 - a repeat flop request warm-starts instead of solving cold.**
  M155 measured the product's real problem: flop p90 64.5s against the
  15-30 seconds a player at a table has to act, with 71 of 73 requests
  paying a full cold solve because hero's class partitions the cache.
  - **Result, same board and successive hero hands:**
    | board | first | later requests | later mean |
    |---|---|---|---|
    | Kd7c2h | 13.2s | 3.0 / 2.5 / 2.9 / 2.8 | **2.8s** |
    | 9dAd5s | 14.9s | 2.8 / 2.5 / 2.3 / 2.5 | **2.5s** |
    | Js6cKs | 14.8s | 2.7 / 2.6 / 2.4 / 2.6 | **2.6s** |
    The first request on a canonical spot still pays full price; this
    shortens the common case rather than removing the cold solve.
  - **How.** `poker_solver/warmstart.py` re-keys a solved tree's
    `node_data` by ACTION PATH - stable across the tree rebuild every
    request does, where `id(node)` is not - and grafts it onto the new
    tree, giving any hand the cached solve lacked a zero row.
    `cfr.solve` gained `initial_node_data`, which works because
    `_solve_recurse` already does `node_data.setdefault(...)`.
  - **Judged against the solver's own noise, not against zero.** Hero's
    force-inclusion moves the solve less than the equity seed does
    (M155: p90 0.002-0.107 against 0.024-0.112). Warm-vs-cold differs by
    **0.0037-0.0147** end to end. 25 refinement iterations was also
    measured and is worse where it matters (0.0898 on one board), so 50
    is a floor rather than the cheapest setting that looked acceptable.
  - **A real bug, caught by an existing guard.** The store was first
    keyed on `(canonical board, canonical stack)` alone - so two
    different preflop action paths, meaning different RANGES and a
    genuinely different question, shared a warm start.
    `test_a_warm_cache_never_answers_a_different_question` failed
    exactly as its own message predicts: "M76's bug in a new field". Now
    keyed on everything that changes the ranges, and deliberately not on
    hero.
  - **What that guard asserts for hero changed, deliberately.** It
    demanded warm == cold, which warm-starting breaks by a noise-level
    amount. The fixture runs 20 iterations over 2 classes, where nothing
    is converged and warm-vs-cold says nothing about bias - drift there
    measured 0.09-0.14 as an artifact of the regime. So the hero case now
    asserts M76's ACTUAL guarantee (hero gets a real answer for their own
    hand, not the previous caller's) and fidelity moved to
    `tests/test_warmstart.py`, where the iteration count makes
    convergence mean something.
  - **Safety properties, each its own test**: a new hand inherits no
    regret from whichever combo held its index; tables whose action count
    changed are dropped rather than reshaped (a different tree is not a
    starting point); `solve` rejects warm data shaped for another pool
    instead of broadcasting; only COLD solves are stored so refinements
    never compound; refinement is clamped to the cold budget.
  - **Two of my own errors on the way**: a missing import that surfaced
    only on the store path, and `id(c)` applied to what were already ids
    after the cache-registration scan changed. 998 backend tests pass.

- **M160 - the flop solve runs in float32, and the real bottleneck is
  named.** The M159 session left the flop as the whole remaining latency
  problem (p50 16.8s, p90 60.1s) and showed M158's warm start does not
  help a solo player, who sees a fresh canonical board nearly every hand.
  So the FIRST solve had to get cheaper.
  - **Anatomy first, per M67's warning that a profiler's obvious target
    can yield zero.** A real flop solve: 318 combos, **16 decision nodes,
    29 terminals**, 11.2ms/iteration, ~700us per node visit, cost linear
    in iterations. The tree is tiny; there is no Python hot loop. The
    cost is `_solve_recurse` carrying a `num_hands x num_hands` matrix
    through every node - ~809KB each in float64, about **18GB of memory
    traffic per 500-iteration solve**.
  - **Float32 on the value path**: 1.12-1.32x measured by interleaved A/B
    in ONE process (M70's rule - a before/after across runs showed
    8.9s -> 6.8s, which is inside this machine's known 1.7x drift and was
    discarded). Worst strategy drift **below 5e-6** across three boards.
  - **Why it is free rather than a trade.** The entries are Monte Carlo
    equity estimates from 30 samples, whose own error is ~0.09 (M98
    measured that class of estimate). Carrying a +/-0.09 quantity in 16
    significant digits preserved nothing; float32's ~7 already exceed
    what the input justifies.
  - **Accumulators deliberately stay float64.** Regret sums accumulate
    across hundreds of iterations and that is where precision matters;
    they are `num_hands x num_actions`, negligible beside the N x N
    transients that dominate bandwidth. Applying float32 indiscriminately
    would risk the numerically sensitive part for no further gain.
  - **This is an improvement, not a fix, and the remaining gap is
    named.** 1.25x takes the flop from ~16.8s to ~13s - still outside a
    comfortable shot clock. Every conventional lever is already closed:
    no Python hot loop (M67), a 16-node tree so pruning is pointless, and
    both accuracy knobs exhausted (M141's cap conservation law, M152's
    precision one). **The real fix is algorithmic**: the recursion
    returns N x N from every node and multiplies it by strategy at each
    ancestor, giving depth x actions x N^2, where vector CFR collapses a
    terminal to an N-vector immediately and stays O(N) above it.
    Estimated 3-5x. Not attempted here: it rewrites the solver the
    canonical library, every reference solve and 999 tests depend on.
  - 999 backend tests pass.

- **M161 - the exact solver propagates a VECTOR, and a real defect
  surfaced on the way.** M160 named the algorithmic rewrite as the whole
  remaining flop-latency fix and estimated 3-5x. Measured, it is 13.07x
  at 321 combos.
  - **The change.** `_solve_recurse` returned a `num_hands x num_hands`
    matrix from every node; the ancestor that needed it multiplied by the
    opponent's reach. Pushing that reach DOWN instead makes every node
    return an N-vector, `u_h[i] = sum_j reach_opp(h)[j] * value(i, j)`.
    The non-obvious step: at a node where the OPPONENT acts the branches
    **sum** rather than average or re-weight, because that player's
    action probability is already folded into the child's own reach.
  - **Speedup scales with the pool, as the O(N^2) -> O(N) argument
    says it must**: 1.15x / 1.28x / 1.77x / 7.56x / 13.07x at 9 / 33 /
    66 / 164 / 321 combos, interleaved A/B in one process per M70's rule.
    Never slower at any measured size.
  - **Equivalence is exact in float64 and deliberately not asserted in
    float32.** Whole trees through `solve()` with each recursion agree to
    1e-9 (single street) and 4.8e-15 (across chance nodes). In float32
    they do not, and three controls establish that this is chaos rather
    than error: each arm is bit-deterministic against itself; the gap
    grows from exactly 0 with iteration count; and it collapses by ~1e9
    at double precision. Against M155's yardstick the gap is inside the
    solver's own noise - at the production seed over four boards, p90
    <=0.003 and 8 entries over 0.05, against seed-only noise of p90
    0.078-0.123 and 637-874 such entries.
  - **The 0.194 worst case is reported rather than the flattering
    number.** On seed 42 - the shipped default - one board's worst entry
    moves 0.194; seeds 1, 2, 3 and 4 give <=0.001 on that same board. An
    earlier harness that happened to pass `equity_seed=1` made the change
    look clean, and the discrepancy was chased down rather than taken.
  - **F45, found by the rewrite.** The first draft computed the second
    player's TRUE payoff at a terminal instead of minus the first's, and
    the chance-node equivalence check moved by 0.97 - identically in
    float32 and float64, which is what proved it was logic and not
    rounding. Cause: `node.pot` carries dead money from earlier streets,
    so the two payoffs sum to that dead pot rather than zero. The offset
    cancels out of every regret difference within one street and does not
    cancel across a chance node into a street whose starting pot depends
    on prior betting. **Reproduced, not fixed** - see CLAUDE.md's F45 for
    why the obvious correction is not safe on its own, and why a
    performance milestone is the wrong place to change what every
    multi-street solve computes.
  - `_solve_recurse_matrix` is kept runnable, with a private
    `solve(_recurse=...)` hook, so the equivalence tests drive the real
    loop rather than a re-implementation of it that could drift.
  - Five new tests, and all four attempted mutations of the new logic are
    caught by them - including the exact dead-pot error above. Full suite
    green at 1,004.

- **M162 - the multiway flop shares its board runouts (~28x), and the
  measurement that made it possible found a bigger problem than latency.**
  M161 left the multiway flop as the slowest thing a player waits for
  (27.8s in the play session). It runs on the sampled solver, which M161
  did not touch.
  - **Anatomy first, and it ruled out the obvious answer.** M161's
    vector rewrite does not transfer: `_mccfr_recurse` already returns a
    length-N vector. Measured instead: a real 3-way flop solve is **99%
    equity lookups** (427 at 12.7ms) against only 42 decision nodes and
    2,296 node visits, and 96% of a lookup is `best_hand_rank_batch`.
    The tree and the traversal are nothing; the hand evaluation is
    everything.
  - **The waste.** `nway_combo_equity_vector` redraws runouts for every
    opponent tuple, so the same candidate is re-ranked on fresh boards
    thousands of times per solve. A candidate's rank on a runout does not
    depend on WHICH opponents it faces - only the comparison does.
    `SharedRunoutRanks` draws runouts once per board, ranks each combo
    once, and turns every lookup into integer comparisons.
  - **26.3x / 31.4x / 26.3x**, interleaved A/B in one process per M70's
    rule (5.32s -> 0.19s on the first board).
  - **Sharing costs effective samples, and that is paid for, not
    ignored.** Shared runouts cannot exclude each tuple's hole cards, so
    collisions are dropped - the same variance-not-bias trade M68 made in
    `_simulate_equity_shared_board` and this module already made for the
    candidate's own two cards, extended to the opponents'. ~23% dropped
    at three-handed against ~8% before, so `SHARED_RUNOUT_SAMPLES = 320`
    rather than 200.
  - **Validated where the answer can be exact.** On turn and river boards
    both implementations enumerate, and dropping collisions leaves exactly
    the deck the per-tuple version enumerated - so equality is required,
    not approximate agreement: **0.0 across 1,420 comparisons**, zero
    NaN-contract mismatches. On flop boards, mean difference 0.020, and
    three controls say noise rather than bias: the reference disagrees
    with itself under a different seed by more (0.036 vs 0.030), signed
    error against a 4,000-sample truth is ~0 for both, and absolute error
    against that truth is lower for shared (0.0156 vs 0.0247).
  - **A correction to this milestone's own first reading.** The initial
    A/B compared the implementation gap against ONE seed pair per board
    and appeared to exceed it on one of three. Measured properly against
    a DISTRIBUTION of seed-pair gaps (p90 0.130-0.326), the
    implementation gap (0.231-0.277 across four seeds) sits inside it at
    or below the median. One seed pair is a single draw, and the one it
    drew happened to be the smallest.
  - **F46, and it is the real finding.** Two solves differing only in
    seed disagree by p90 0.473 at the shipped budget, 0.449 at 1,000
    iterations, and **0.313 at 4,000** - twenty times the shipped budget,
    worst case still 0.959. Multiway flop advice is substantially noise.
    The budget was deliberately not raised here: 4,000 iterations is now
    affordable (~1.5s, still cheaper than today's solve), but "more
    stable" is not "more correct" - M152 measured that exact trap - and
    scoring it needs a converged multiway reference that does not exist.
  - Five new tests (six cases with parametrisation); all four attempted
    mutations of the sharing logic are caught by them. Full suite green
    at 1,010.

- **M163 - the recommendations pass, and two of the six were wrong.**
  Worked through the six recommendations the three-session report
  produced, in order.
  - **R1: the mid-flop node was 29x slower than the opening decision on
    the same board, and the reason was not the one the report gave.** The
    report blamed a missing warm-start cache. That was true but minor;
    the real cause is that this call passed neither `equity_samples` nor
    `equity_table_fn`, so it built its equity table at `board_equity`'s
    default of 200 samples, sequentially, where the opening decision uses
    30 through the parallel builder. M131 and M132 each fixed exactly
    this for the canonical-library path and neither reached here.
    Measured through `/advise`: **35.08s -> 1.22s** cold, **34.55s ->
    0.84s** for a second hero. Also a consistency fix, since M88 exists
    to make both flop decisions model the same game and equity precision
    is part of that game.
    Validated: 30 samples moves the answer LESS than a different equity
    seed does (p90 0.088/0.052 vs 0.103/0.085), and warm-starting moves
    it p90 0.0003 with no entry over 0.05.
  - **R2 / F47: an untrained uniform row read as "high" confidence.**
    M149's guard was gated on `trained is True` because an untrained hero
    "already fires a louder warning" - which lives in a FIELD that
    `solver_confidence` never consulted. Both causes now report low, with
    different reasons. Required deliberately overturning an assertion
    M149 wrote to pin the old behaviour, which is that guard working as
    designed.
  - **R3: multiway flop nodes are trained on demand**, the postflop
    sibling of M150, at 400 iterations. Affordable only after M162. The
    defect is rare (1 in 837 decisions), so mechanism and wiring are
    tested separately rather than pretending to an end-to-end repro - and
    the wiring test immediately caught that the test's preflop path
    folded to two live players and never reached the multiway cell.
  - **R5 was NOT a defect, and the report's own grouping invented it.**
    The report noted multiway facing-a-bet at 17.4s against 0.78s for the
    opening decision, and flagged that `api/solving.py`'s comment claims
    that cell needs no new solve. Measured: the facing-a-bet node costs
    **0.01s and triggers zero solves** - the comment and the code are
    both right. The session cost is the FIRST request on a board paying
    the cold solve; multiway players often face a bet immediately, so
    that decision carries it. Grouping session latency by node type
    attributed a cold-start cost to a node type.
  - **R4 failed to build a converged reference, and that is the finding.**
    Seed spread across budgets: 0.462 / 0.295 / 0.333 / 0.308 / **0.240**
    at 200 / 1k / 4k / 12k / 30k - still 0.240 at 150x the shipped
    budget, non-monotone. But a SAME-SEED comparison across budgets does
    converge (0.0118 at 12k vs 30k), so the solve settles to a
    seed-dependent answer rather than to an answer.
    Splitting the sources: **MCCFR traversal seed 0.467 / 0.357 / 0.328;
    equity seed 0.321 / 0.231 / 0.205.** Sampling noise is the larger
    term - the opposite of the hypothesis this milestone started with,
    which was that M98's frozen-equity mechanism would dominate.
    More samples help partially (9.4x samples moves equity-seed spread
    0.212 -> 0.170 and 0.295 -> 0.136 across two boards, at 2.4x cost)
    and are NOT adopted: they leave the dominant term untouched, and
    "better" cannot be scored without the reference this exercise failed
    to build.
    **One sweep reported 0.045 at 3,000 samples; replication across four
    seed pairs showed that was a single pair's luck** (the others: 0.20,
    0.17, 0.17). Three points were nearly read as a trend for the second
    time in this project's history.
  - **R6 is a method change, applied here and recorded in CLAUDE.md:**
    three sessions, not one, for any quality claim. Timing repeats
    (median 0.81-0.90s, heads-up flop 1.15-1.17s across three runs);
    defect counts do not (uniform rows 0/3/2, stack-commits 20/12/10),
    and F47 appeared in one run of three.
  - Eight new tests, taking the suite 1,010 -> 1,018. Every attempted
    mutation of the new logic is caught by them - nine mutations across
    the three code changes.

- **M164 - the M163 trainer was passing the wrong key type and did
  nothing.** `StrategyResult.strategy_at` keys by `str(hand)`. M150's
  preflop trainer passes `str(_combo_to_class(hero_combo))`; M163's
  postflop sibling passed the `HandCombo` object, so
  `strategy.get(hero_key)` never matched, `hero_row` was always None, and
  the hero-row trigger could not fire. The function still ran on its
  second, much rarer condition ("nothing at this node is trained"), which
  is precisely the branch M163's own tests exercised - so a full suite
  and four mutation tests all passed while the feature was inert.
  - **Found by re-running the three sessions and comparing counters,
    not by a test.** Uniform-prior rows came back **0/3/2 both before and
    after M163 - byte-identical, same hands, same nodes**. A single run
    would have reported "0 defects" and looked like success; the
    like-for-like three-run comparison is what made the absence of change
    visible. This is the R6 rule earning its place immediately.
  - A first hypothesis - that these were 6-max hands folded down to two
    live players, taking the heads-up cell - was **wrong**, and checking
    it is what surfaced the real cause: the captured requests showed six
    live positions, so the multiway cell was reached and the trainer was
    being called; it was declining to act.
  - Measured after the fix, on the exact failing request (hero ATo on
    As Ks 5h facing a raise): **0.3333 / 0.3333 / 0.3333 -> fold 0.0018 /
    call 0.0405 / all-in 0.9577**, `solver_confidence` low -> high. Top
    pair had been folding a third of the time.
  - **The lesson is about what the tests asserted.** They proved the
    trainer works when it runs and that the call site reaches it; neither
    proves it runs for the reason it exists. The new guard asserts the
    KEY TYPE at both call sites, and restoring the object key fails it.
  - Two new tests, suite 1,018 -> 1,020. Three mutations, all caught.

- **M165 - the river's uniform rows, and two wrong conclusions on the way
  to them.** The three-session comparison left five uniform-prior rows:
  three on the flop (M163/M164) and two on the river.
  - **Different cause from the multiway case.** The heads-up river runs
    the EXACT solver, which visits every hand at every node - so
    `trained` is true and the row is still exactly uniform, because every
    regret stayed <= 0 through the chained solve's 20 iterations. F43's
    mechanism in the exact solver.
  - **Measured on the real failing request** (hero Jc9c, 3d Kc 4d / 4s /
    8c): 10 of 19 hands at the node were the bare prior, hero included,
    returning `check 0.5 / all-in 47.5bb 0.5` with jack-high. After
    `_ensure_exact_node_trained`: **check 0.99995**, node 10/19 uniform
    -> 0/19, trained 19/19, `solver_confidence` low -> high. The subtree
    converges at ~50 iterations (0.9992) so 200 is comfortable margin.
  - **First wrong conclusion: "the turn inverts".** Extending the same
    repair to the turn looked like obvious thoroughness, and a sweep
    showed value hands betting small while air jammed and nothing
    checked. That sweep asked as a different hero each time; hero is
    force-included into the range, so every row came from a different
    solve. It compared five solves, not one node with and without
    training. Withdrawn.
  - **Second wrong conclusion, from a broken control.** The first attempt
    to fix that A/B matched hands by `set(cards)`, which cannot tell
    `7s2h` from `7h2s`, and read a different hand's row while reporting
    `fired=False`. Also withdrawn.
  - **What is actually true about the turn**: the trainer would never
    fire there. Hero's row is always trained because hero is
    force-included; the uniform rows belong to hands the user never sees.
    A/B with hero held fixed and only the trainer toggled: **0 firings
    across five heroes, byte-identical strategies**. The turn call site
    was built, measured, and removed, with the finding recorded in place.
  - The through-line: two confident conclusions from comparisons where
    more than one thing varied - the hero key in M164, hero itself here.
    Both times the tell was a number moving when the thing under test had
    not changed.
  - Three new tests, suite 1,020 -> 1,023. Four mutations, all caught,
    including passing the flop's equity table instead of the river's.

- **M166 - a ten-game benchmark, a withdrawn accusation, and a signal
  instead of a cure.**
  - **Ten full games** (1,200 hands, 2,733 decisions, seeds 31-40),
    finally enough volume to put a rate on defects that had been
    appearing and vanishing between single runs.
  - **Speed is finished as a problem.** p50 **0.86s**, p90 5.05s, and the
    worst of 2,733 decisions was **15.7s** - 100% inside 16 seconds,
    99.9% inside 10. Nine of ten games had a worst case under 7s. A hand
    taken to the river costs ~9s of waiting spread over four betting
    rounds. Zero uniform-prior rows in 1,200 hands (M163-M165 held), one
    flagged decision in 2,733.
  - **A WITHDRAWN finding, and the same error this project logged
    twice before.** The benchmark reported the big blind folding 8%
    facing a raise heads-up and called it broken, against a remembered
    "25-35%". That figure belongs to larger raise sizes. Two measurements
    say the advice is right: a best response against the button's real
    solved range folds **6.3%** where the advisor recommends 5.3%, worth
    **0.0021 bb/hand**; and the defence tracks the price correctly -
    fold **0.1% / 5.3% / 38.2% / 55.9%** as the open goes 2.0 / 2.5 / 3.5
    / 5.0bb. M110 and M111 made the same remembered-reference mistake;
    this is the third. **Establish the reference for THIS tree before
    calling a frequency wrong.**
  - **The real defect is postflop weak hands, and it is not fixable by
    reweighting.** 27 spots across two studies: strong and medium bands
    never exceed 0.10 error, the weak band averages 0.279 and reaches
    0.903, in BOTH directions (betting air that should check, checking a
    flush draw that should bet 99.3%).
  - **The tenth attempt at the cap died here.** Hero is out of the capped
    range on 97% of postflop decisions, so including hero's whole class
    looked like the missing fix. Two spots improved, two got much worse,
    mean error 0.400 -> 0.571.
  - **What shipped is calibration, not a cure.**
    `poker_solver/hand_strength.py` plus a band note leading the postflop
    caveat and a `hand_strength_percentile` field. The threshold is the
    measured band boundary. Ranks against every possible hand rather than
    the modelled range, on purpose.
  - **Two process notes.** A mutation of the turn's six-card path was
    MISSED by the test written for it - the test compared a pair against
    air, which passes whether or not the turn card is used. Rewritten
    around a hand the turn makes (deuces becoming a set), it catches the
    mutation - and its first assertion was itself wrong: the lowest pair
    scores 0.612, not below 0.55, because most random hands miss the
    board entirely. The code was right; the guess was not.
  - Fourteen new tests, suite 1,023 -> 1,039. Six mutations attempted,
    five caught immediately and the sixth after the test was rewritten.

- **M167 - the calibration M166 shipped was not supported, and this
  withdraws it.** Five confirmation games (600 hands, 1,407 decisions)
  showed the signal working exactly as built: a percentile on 807/807
  postflop decisions, every warned hand below the threshold and every
  unwarned one above, no boundary violations, speed and defect counts
  unchanged. Then it fired on **52% of postflop decisions**, which is a
  warning a player stops reading, so the next question was whether error
  is graded inside the weak band.
  - **It is not, and the control band was worse than the band it
    controlled for.** Sub-banding gave 0.0606 / 0.0058 / 0.0628 across
    0-0.20 / 0.20-0.40 / 0.40-0.55, and **0.2495 for the 0.55-0.75
    "reliable" control**, whose worst spot missed by 0.9903.
  - **Pooled over 44 spots from three studies, strength and error
    correlate at -0.130.** Errors appear at every band below 0.65,
    including one at 0.64 that M166 would have certified. M166's split
    rested on 27 spots and the next 18 broke it.
  - **What survives is one-sided**: 0 of 9 spots at or above 0.75
    exceeded 0.10 error (mean 0.0144, worst 0.0571); below it, 10 of 35
    did, worst 0.99.
  - **The claim is inverted rather than retuned.** `/advise` now
    certifies reliability where it was measured and says reliability is
    NOT KNOWN elsewhere, quoting the rate ("about one in four... the
    worst by 99") instead of a verdict on the hand in front of the
    player. Tightening 0.55 to 0.75 would have kept an unsupported claim
    and merely moved its boundary.
  - The 52% firing problem is fixed as a CONSEQUENCE, not a goal: the
    certifying note lands on the minority that earned it. Adjusting a
    threshold because a rate felt high would have been the same error as
    judging the big blind against a remembered chart.
  - Suite green at 1,039.

- **M168 - the reliability certification was false on the turn, and is
  now flop-only.** M167 measured its threshold on flop spots and applied
  it to all three streets because checking the others was expensive.
  Checked, on eight turn spots drawn from real play, the relationship
  **inverts**:
  | band | n | mean err | worst | over 0.10 |
  |---|---|---|---|---|
  | not known (<0.75) | 4 | 0.0728 | 0.138 | 1 |
  | certified (>=0.75) | 4 | **0.2960** | **0.588** | **3** |
  A hand at percentile 0.977 (Qs2s on 3s2dQh/5h) was off by **0.588**
  while the response told the player the advice measured reliable. That
  is worse than saying nothing.
  - **Certification is now flop-only.** Turn and river get a note that
    says accuracy there is unmeasured *and why that matters* - strength
    does not carry over, and on the turn the hands this would certify
    were the least accurate tested.
  - **The asymmetry that decided it**: certifying reliability needs
    positive evidence for the street; withdrawing a certification needs
    only the absence of it, and here the evidence actively contradicts.
    Four spots per band is a thin sample to conclude from and an ample
    one to stop claiming on.
  - **The river is uncertified without being measured.** After the turn
    inverted, assuming the river resembles the flop would be the same
    mistake a second time.
  - Third correction to this feature in three milestones (M166 claimed a
    strength split, M167 withdrew it and kept a one-sided claim, M168
    scoped that claim to one street). Every correction came from a
    measurement, and every claim was shipped one measurement ahead of its
    evidence - the lesson is the publishing order, not the analysis.
  - Four new tests, suite 1,039 -> 1,043. Three mutations, all caught.

- **M169 - seed-averaging for F46: built, measured, left off.** Working
  the recommendation list in order. Items 1-3 first:
  - **Item 1 (confirm the calibration) closed by M167/M168.**
  - **Item 3 was WITHDRAWN as a mistake of mine.** The ten-game report
    criticised the product for labelling all 2,733 answers "high
    confidence". That was `solver_confidence`, which reports whether the
    solve RAN. `aggression_confidence` was separately reporting **low on
    every postflop decision** the whole time, and the caveat below it now
    distinguishes all three cases. Two different axes, both correct; I
    conflated them. Nothing to implement.
  - **Item 4, F46's instability.** Averaging independent solves is the
    textbook answer to sampling variance and was untried. It works in the
    sense that matters most for trusting it: the spread keeps shrinking
    with K at or beyond 1/sqrt(K) (0.450/0.250/0.202/0.119 at K=1/2/4/8),
    so the runs scatter around a stable centre rather than landing on
    different equilibria.
  - **And it is off by default anyway**, on three measurements: the worst
    case does not improve at ANY K (0.88-1.00 throughout) so the
    disagreements a player notices survive; the gain is sub-sqrt(K)
    (1.42-1.61x at four runs); and it costs 4x latency on the street
    M162/M163 made fast - live, a multiway flop goes 0.8s -> 3.2s - while
    making the answer more reproducible rather than more correct.
  - **A prototype of mine reported 2.3-2.5x and was inflated.** It meaned
    `average_strategy()`, which returns the uniform prior for untrained
    rows; 47% of rows are untrained, so it was averaging real strategies
    toward something identical in every run. The shipped form adds
    strategy sums, keeps untrained rows at zero, and measures lower.
  - **Two guards initially could not fail, and both were rewritten.** The
    untrained-row test used one iteration, where runs touch mostly
    disjoint nodes, so the merge branch barely fired; it now uses 60 and
    compares by ACTION PATH, since each solve builds its own tree. The
    independence test passed under a mutation that reused one traversal
    seed, because the equity seed still varied - it now inspects the
    calls and asserts both vary.
  - Five new tests, suite 1,043 -> 1,047 (plus one rewritten). Five
    mutations, all caught after the rewrites.

- **M170 - the turn gets a sized re-raise, reversing M156 on both of its
  own premises.** Item 5 of the recommendation list, and the first change
  in this pass that is an accuracy improvement rather than a correction or
  a measured "no".
  - **The defect (F38, diagnosed by M156):** `FLOP_TURN_RAISE_SIZES=(2.5,)`
    at `FLOP_TURN_MAX_RAISES=2` gave the turn one raise size, so facing a
    bet the only aggressive action left was a ~97.5bb shove. A hand that
    wanted to raise a third of the pot had no button for it and the
    solver put weight on the only one there was.
  - **M156 built this exact change and declined it**, for two stated
    reasons: latency was the top complaint, and adjudicating needed a
    converged turn reference that did not exist. **Both expired.**
    M162/M163 cut turn latency (the worst decision in 1,200 hands is now
    15.7s); M168 built a turn reference and showed it holds still.
  - **Re-measured on the axis that costs money** - how often the advice
    commits the whole stack facing a bet, which is F38's actual symptom -
    over 14 turn spots drawn from real play, against a wide-range
    reference on the richer tree, every reference stability-checked:
    **mean error 0.1471 -> 0.1069, better on 12 of 14.**
  - **The cost premise inverted too.** M156 measured 18-20s -> 28-32s
    (1.5x). The solver changed underneath it, and the same comparison now
    measures **1.43s -> 1.62s (1.13x)**. A turn decision goes from ~5.0s
    to ~5.2s end to end.
  - **M156's decision was right on its evidence and wrong on today's.**
    It judged three hands and saw "fixes middle pair, worsens top pair" -
    M141's conservation pattern. Scoring 14 spots on the commit axis
    against a reference gives a different and clearer answer.
  - **Two guards fired, both correctly.** `test_docs` caught CLAUDE.md
    still claiming `FLOP_TURN_MAX_RAISES = 2`. M127's byte-ceiling test
    caught the cache: a wider tree is a bigger entry, `turn_path` went
    7.59 -> 11.01 MB, and 20 of those is 220 MB against a 168 MB budget,
    so the ceiling dropped 20 -> 14. That is the first time a config
    change has tripped it.
  - **The RIVER still has the original problem and was deliberately not
    touched.** `FLOP_TO_RIVER_RAISE_SIZES = ()` leaves check-or-shove as
    the whole menu (F40), but `solve_flop_to_river` takes one
    `raise_sizes` for all three streets, so widening the river widens the
    flop and turn again - and that chain's cost has not been re-measured
    since M161/M162. Same question, same method, not assumed to transfer.
  - Suite green at 1,048.

- **M172 - the range cap works at 100 classes, and the eleven failures
  before it were all measuring the wrong regime.** The largest accuracy
  improvement this project has made, and it came from a measurement
  nobody had taken.
  - **The question that unlocked it**: how much of the opponent's range
    does the cap actually KEEP? Never measured in eleven attempts.
    Answer: the derived range has all 169 classes nonzero and the top 26
    carry a **median 28% of the mass**, worst 15%. Every selection rule
    ever tried was choosing which quarter to keep.
  - **The frontier**: error 0.2005 / 0.2169 / 0.2685 / **0.1065** /
    0.0956 at caps 26 / 44 / 60 / 100 / 140. Error RISES to 60 -
    M141's conservation law, faithfully reproduced - then halves at 100,
    where coverage reaches 95%. M137 stopped at 60 and concluded width
    stops paying; true of what it tested, false generally.
  - Measured against the UNCAPPED 169-class solve at 200 samples and
    2,500 iterations, ~65s per solve, two per spot, drifting references
    discarded. Better on 8 of 12 spots, worse on 3; over-0.10 spots
    4/12 -> 2/12; worst 0.9904 -> 0.5924.
  - **Iterations barely matter once covered** (0.1065 at 500 vs 0.1090
    at 250), so half of them are bought back to offset the cost. M131's
    three-way budget needed revisiting, not rebalancing.
  - **Cost, measured end to end and NOT from the solve in isolation**:
    heads-up flop 1.02s -> 6.37s, **6.2x**, where the isolated solve
    predicted ~4x. Third time this session a lab measurement
    under-predicted the request (M170 was 1.13x -> 1.36x). Turn and river
    unaffected at 1.02-1.04x.
  - Five sessions, 1,325 decisions: inside 5s 86% -> 65%, inside 10s
    ~100%, worst 9.17s -> 10.20s, **zero defects and zero uniform rows**.
  - **No middle ground exists.** Cap 60 is worse than 26, so the choice
    is 26 or 100+. That is a property of the threshold, not of tuning.
  - The byte-ceiling guard fired for the second milestone running:
    `canonical_warm_starts` entries grew to 1.23 MB against a 168 MB
    budget, so its ceiling dropped 256 -> 128.
  - **This also answers the data-versus-compute question.** The engine's
    central defect was reachable by compute; the ranges were not wrong,
    only unexamined. A hand-history layer would have been solving a
    problem we did not have.
  - Suite green at 1,048.

## M173 — the turn solved as its own street

The turn was the product's worst decision on every axis at once: the
**slowest** (7.94s median across five sessions, against the flop's 6.38s
and the river's 4.70s), the **thinnest** (a cap of 4 classes, keeping a
median **4.6%** of the opponent's range mass against the flop's 95% after
M172), and **57.7% of all postflop advice**. It had also never been
validated against a reference — every postflop accuracy study in this
project measured the flop.

Chaining is what made it thin. `solve_flop_turn` solves the flop AND the
turn, linked through chance nodes, so cost explodes with range width:
cap 8 costs 3.3x, cap 14 8.8x, **cap 26 40x (66 seconds)**. Coverage was
simply unreachable by that route, which is why the cap sat at 4.

`solve_flop` already solves ONE street with remaining runouts averaged at
the terminal, and nothing in it assumes three board cards — a four-card
board is resolved **exactly** by `build_board_equity_table`
(`remaining_needed == 1`, M154). Solving the turn that way is **47x
cheaper at equal coverage** (cap 26: 1.40s standalone vs 66.46s chained).

Measured on 10 turn spots from real play against a full-range standalone
reference, all arms at 250 iterations:

| coverage | mean err | median | over 0.10 | solve |
|---|---|---|---|---|
| cap 4 (was shipped) | 0.3601 | 0.3274 | 7/10 | 0.07s |
| cap 14 | 0.1836 | 0.0427 | 4/10 | 0.33s |
| **cap 26 (adopted)** | **0.1361** | **0.0399** | **3/10** | **0.28s** |
| cap 44 | 0.4102 | 0.2474 | 5/10 | 0.71s |
| cap 60 | 0.2492 | 0.0311 | 4/10 | 1.24s |

Non-monotone past 26 — M141's conservation law, not the flop's clean
threshold — but the shipped setting is the **worst arm tested**. Against
it, cap 26 standalone is 2.6x more accurate and 6x cheaper.

**The flop tree is built but never solved.** Everything the turn needs
from the flop — the terminal's pot, who folded, how much each player
invested — is structural, fixed by the tree's shape rather than by any
strategy. `build_street_tree` builds children lazily, so walking one
action path materialises only that path.

### Validated in play, and the isolated number under-predicted the gain 7x

Three 120-hand sessions, 837 decisions, against five pre-M173 sessions
(1,307 decisions):

| street | before | after | |
|---|---|---|---|
| flop | 6.38s | 6.71s | 0.95x |
| **turn** | **7.94s** | **0.87s** | **9.11x** |
| river | 4.70s | 4.66s | 1.01x |
| turn max | 9.68s | **1.41s** | |
| over 8s | 9.3% | **3.1%** | |
| over 5s | 35.3% | **22.7%** | |

**The 0.28s-vs-1.67s solve figure predicted ~1.2x end to end and the real
gain is 9.1x**, because the chained request was also paying for a flop
solve that standalone skips entirely — the isolated measurement showed
only part of what was removed. Every other cost prediction this session
UNDER-quoted the request (M170 1.13x isolated -> 1.36x real; M172 4x ->
6.2x); this one over-quoted it, for the same underlying reason each time:
the isolated arm and the request are not measuring the same work.

Zero defects, zero uniform rows across 837 decisions.

**Turn strategies got more decisive, and it is convergence rather than
collapse.** Mixed strategies (top action below 0.9) fall 37.3% -> 22.1%,
but the split says which: the genuinely-indifferent band (0.40-0.60) is
UNCHANGED at 7.8% -> 8.6%, while the half-resolved middle (0.60-0.90)
drops 28.5% -> 13.6% into near-pure. A degenerate solve flattens the
indifferent spots too. This corroborates the reference measurement above;
it does not independently establish it.

### What is NOT resolved, stated rather than buried

The reference is a standalone full-range solve, so this measures "does
standalone converge as coverage rises", **not** "is standalone right
versus chained". That comparison cannot be run: at the only coverage
where chained is affordable (cap 14) both arms are unstable, and there
they agreed on 6 of 8 spots while disagreeing on 2 in OPPOSITE
directions — which reads as noise rather than the chain carrying
information. What standalone gives up is the flop betting round's
influence on the turn strategy.

`TURN_SOLVE_STANDALONE = False` restores the chained path exactly. The
flag exists because that question is genuinely open, not as a migration
aid.

### A guard that mutation testing caught

`test_the_standalone_turn_solves_at_the_wider_coverage` exists because
reverting the cap from 26 to 4 — which silently undoes the whole
milestone — broke **nothing**. Every existing turn test checks structure
(does it resolve, does it refuse bad input); none checked coverage, the
thing this milestone exists to change. Verified by re-running the
mutation after adding the test.

Illegal turn cards needed an explicit check for the first time: the
chained path got that free, since a card already on the board simply had
no chance branch to look up. Standalone has no branch list.

## M174 — the river solved as its own street

The sibling of M173, one street further, and the defect it repairs is the
worst one found in this project: **the chained river recommended
committing the whole stack into a 2-5bb pot on 4 of 12 real spots, at
27-58% frequency.** `6hAc` on Qs5c4h/3h/9c shoved 17.5bb into a 5bb pot
**53% of the time** where the correct play is to check 0.9999.

The river carried the tightest budget in the product - **9 COMBOS** per
side, against the flop's 100 classes and the turn's 26 - and was the only
street modelling no bet size at all (F40). Both come from one cause: it
was the third leg of a chained solve, and `solve_flop_to_river` takes ONE
`raise_sizes` for all three streets, so widening the river widened
everything.

Solved on its own it is the CHEAPEST street, not the most constrained:
the board is complete, so `build_board_equity_table` takes its
`remaining_needed == 0` branch and equity is **exact** - no Monte Carlo,
`equity_samples` ignored entirely (M154).

### Measured as an interleaved A/B through /advise, one flag apart

12 real river spots, each scored against a full-range 169-class reference
built at **that request's own pot and stack**, solved twice.

| arm | strong | weak | ALL | over .10 | latency |
|---|---|---|---|---|---|
| chained (was shipped) | 0.2550 | 0.1347 | 0.1948 | 7/12 | 12.18s |
| **standalone cap26 + sizes** | **0.1214** | **0.0038** | **0.0626** | **3/12** | **0.65s** |
| standalone cap26 no sizes | 0.1046 | 0.0020 | 0.0533 | 2/12 | 0.50s |

**3.1x more accurate and 19x faster**, better on 10 of 12 spots.

### Coverage did the work; the sizes are a WASH and are labelled as one

Varying them independently, the sizes are not separable at this sample:
paired delta **+0.0093 +/- 0.0181 (sem)**, better on 3 spots, worse on 2,
tied on 7. They are adopted because they close **F40** - the river could
not answer "how much should I bet" - at no measured accuracy cost and
+0.15s. `RIVER_STANDALONE_RAISE_SIZES` says so in the config, so nobody
later reads them as the improvement. What fixed the accuracy was
coverage: 9 combos -> 26 classes.

M144's own test anticipated this exactly, saying "if intermediate sizes
now exist this test should be revisited rather than deleted, and the
disclosure below relaxed". The `BET_SIZING_COVERAGE_NOTE` stopped firing
**on its own**, because M144 derived it from the response's own rows
rather than from the config constants.

### A real tradeoff, paid for by a measurement

Standalone keys its cache **per board**, so the chained solve's reuse
across every turn and river card off one tree is genuinely gone. What
repays it: a chained entry cost **38.45 MB** and a standalone one costs
**0.42 MB** - 92x smaller - so the ceiling went **4 -> 256**. The cache
holds 64x more boards for two thirds of the memory, and each solve is
~19x cheaper to recompute anyway.

### Three harness errors, all caught before they became findings

Worth recording because each one nearly produced a published number:

1. **The first study's "shipped" arm was not the shipped path.** It used
   `solve_flop` at cap 3 on a fixed pot=18/stack=85; shipped is a chained
   3-street solve at 9 combos and ~20 iterations, and the real requests
   are at 20/50/100bb with their own pots. Checked against production,
   the proxy disagreed on **2 of 3 spots** - it shoved 0.9986 where
   production checks 0.906 and 0.842. This is M164's failure exactly, and
   the claim built on it ("shipped shoves 99.9% with strong hands") was
   withdrawn.
2. **The size question was asked on a set where the answer is "check".**
   11 of 12 spots had reference betting below 0.05, and the reference
   itself had no sizes - so it said "check" even at percentile 0.79. An
   aggregate "betting frequency shift" over that set cannot show
   anything. Found by reading the rows, which showed the sized bet
   replacing the shove entirely while the total barely moved.
3. **The A/B first read the wrong dict.** `strategy` is the whole node
   keyed by HAND; hero's row is one entry in it, under the CANONICAL
   combo spelling ("Kh6h", not the request's "6hKh"). Both mistakes
   returned "no answer" for real answers.

### The guard, and the mutation that survived its first version

`_query_river_standalone` omitted `river_iterations`, which `/advise`
reads - and the KeyError is caught and reported as **"unsupported
street/table-size combination"**, pointing at the table size when the
cause is a missing field. Every standalone river request 422'd.

The first guard checked for the string in the source and **the mutation
survived**, because the same literal appears in the helper's terminal
branch. The second checked behaviour but still missed "delete the
dispatch entirely" - which falls through to the chained path and also
returns 200. It now identifies the standalone path by a sized raise the
chained path cannot offer. Three mutations, all caught.

## M175 — the turn re-measured: still uncertified, for a corrected reason

M173 made the turn 9x faster and 2.6x more accurate. The obvious next
question is whether it is now accurate enough to CERTIFY — to tell a
player, as `/advise` does on the flop, that this particular answer was
measured reliable. That is 28.9% of all postflop advice.

Two things made M168's refusal worth re-testing rather than trusting:
the ARM it judged saw 4 classes per side (a median 4.6% of the
opponent's range mass, M173's worst measured configuration), and the
REFERENCE it judged against was itself capped, at 14 classes chained.
Neither side of that comparison exists any more.

**The answer is still no, and the rule was fixed before the data
arrived**: certify only if ZERO spots at or above percentile 0.75 exceed
0.10 error, which is the bar the flop cleared (9 spots, worst 0.0571).

24 turn spots from real play, 12 per band, each against a full-range
169-class standalone reference solved twice. **Every reference held at
drift 0.0** — none discarded.

| band | n | mean | median | worst | over .10 |
|---|---|---|---|---|---|
| below 0.75 | 12 | 0.1032 | 0.0141 | 0.5541 | 2 |
| **at or above 0.75** | 12 | **0.0954** | 0.0533 | **0.3038** | **4** |

4 of 12 in the band a certificate would vouch for, worst 0.3038 at
percentile 0.884. Refused.

### What changed is the REASON, and it is user-visible

M168 reported an INVERSION — the reliable-looking band was the worst one,
3 of 4 spots over 0.10, worst 0.588 — and `UNMEASURED_STREET_NOTE` told
players so. At 12 spots per band that is false. The two bands are
indistinguishable (0.0954 against 0.1032) and the strength/error
correlation is **+0.057**, against the flop's -0.130. Strength does not
invert on the turn; it carries **no signal there at all**.

Both statements refuse certification, so this changes no behaviour — but
only the true one stops a reader concluding that WEAK turn hands are the
safe ones, and 2 of 12 of those exceed 0.10 error too. The note now says
the advice was off by more than 0.30 at BOTH ends of the strength range.

This is M166's error in miniature, one more time: a claim about how error
splits by hand strength, drawn from 4 spots per band, that did not
survive 12. That is now the **fourth** finding in this project overturned
by measuring more of the same thing.

### Recorded and pinned

`TURN_CERTIFIED_BAND_WORST_ERROR`, `TURN_CERTIFIED_BAND_SPOTS_OVER_TENTH`
and `TURN_RELIABILITY_SPOTS_PER_BAND` record the measurement;
`TURN_RELIABILITY_QUOTED_WORST` is rounded DOWN so the user-facing number
is one the measurement actually reached. Two guards, four mutations, all
caught: certifying the turn without evidence, overstating the quoted
worst, thinning the evidence back to 4 spots per band, and restoring the
withdrawn inversion wording.

### The hypothesis this leaves

The flop's error only halved once coverage reached 95%; below that, more
width made it worse (M141's conservation law, M172's frontier). The turn
is at 28%. So "the turn needs the flop's coverage, not merely more than
it had" is the natural next test — and it is now cheap to run, since a
turn solve costs 0.28s. Not attempted here: this milestone answers the
certification question it set out to answer, and adopting a wider turn
cap needs its own frontier and its own latency budget.

## M176 — shared runouts for the heads-up equity table

Recommendation 3 of the ten-game benchmark was "attack flop latency":
after M173 and M174 removed the turn and river from the slow population,
the flop was **42% of postflop advice, 6.71s median, and 100% of every
decision over 8s**.

### The anatomy inverted, and the documented constraint was backwards

Measured through `/advise` at cap 100, instrumented on the shipped path
rather than a reconstruction:

| | M155 (stale) | measured now |
|---|---|---|
| equity table | 14% | **89.5%** (7.71s of 8.62s) |
| CFR solve | 86% | **4.8%** (0.41s) |

CLAUDE.md said "anything aimed at flop latency must attack the CFR
solve; caching or extending the equity table caps out at 14%." That was
true when M155 measured it and is now the opposite of true — **M161**
made CFR O(N) instead of O(N^2) (13.07x) and **M172** tripled the combo
count the O(N^2) table build scales with. Two changes pushing the same
way turned the dominant cost into the negligible one. The constraint is
corrected in this milestone; left alone it would have sent the next
attempt at 4.8% of the cost.

### The fix is M162's, applied to a path it never reached

`build_board_equity_table` drew FRESH runouts for every (i, j) pair and
ranked both hands on them. **A combo's rank on a runout does not depend
on who it is compared against — only the comparison does.** M162 removed
exactly this from the multiway path for 26-31x and nobody carried it
across. `build_shared_runout_equity_table` draws runouts once per BOARD,
ranks each combo once, and reduces every pair to integer comparisons.

Interleaved in one process (M70), against the per-pair builder at its
shipped 30 samples:

| cap | combos | per-pair | shared | |
|---|---|---|---|---|
| 26 | 164 | 1.25s | 0.13s | 9.3x |
| 100 | 708 | 53.87s | **1.30s** | **41.6x** |

End to end through `/advise`, median of 10 real flop spots:

| | before | after | |
|---|---|---|---|
| cold request | 8.62s | **3.24s** | 2.7x |
| different hero, same spot | 7.17s | **1.75s** | 4.1x |
| equity table | 7.71s (89.5%) | 1.56s (48.1%) | 4.9x |

### It is more ACCURATE as well as faster, on all three boards

320 shared samples net more usable runouts than 30 per-pair ones. Against
a 4,000-sample truth:

| board | per-pair s30 | **shared s320** | per-pair vs ITSELF |
|---|---|---|---|
| Th5s7c | 0.0496 | **0.0166** | 0.0679 |
| KsKcQh | 0.0481 | **0.0130** | 0.0677 |
| 2h6d9c | 0.0473 | **0.0173** | 0.0658 |

**The shipped builder disagrees with itself under a different seed by
more than the new one disagrees with the truth**, so this change sits
inside the noise it replaces while cutting that noise ~3x.

### Correctness is checked where it can be EXACT

Shared runouts cannot exclude a pair's hole cards, so colliding draws are
DROPPED — rejection sampling, which yields exactly the conditional
distribution the per-pair builder enumerates. On TURN and RIVER boards
both forms enumerate (`remaining_needed <= 1`, M154), so there is nothing
left to differ: **8,460 cells across two boards agree at 0.00e+00**, NaN
contract included. On flop boards the two can only agree within Monte
Carlo error, so agreement there would prove nothing — which is why the
enumerated case is the evidence.

`SHARED_RUNOUT_FLOP_SAMPLES = 320`, not the caller's per-pair 30.
Forwarding 30 would still build a table, still be deterministic, and
still pass every other test while being ~10x noisier than intended, so it
has its own guard.

### Two process notes

**The isolated number over-promised, for the fourth time this session.**
The first shared build measured 15.59x isolated and **1.29x end to end**,
because production was already getting M132's 4.79x from parallelism — so
the real comparison was parallel-per-pair against sequential-shared. What
closed the gap was vectorising the remaining Python pair loop (~450k
iterations at cap 100) and the per-(combo, runout) layout loop (~300k):
41.6x isolated, 2.7x real. The algorithmic win was never the whole story;
the Python overhead around it was.

**A guard passed while its mutation survived.** `test_the_shared_runout_
path_is_what_the_flop_actually_uses` was first written on a TURN board —
where both builders agree exactly by construction — so disabling the flag
entirely still matched. It needs a FLOP board to distinguish them. Four
mutations now caught: ignoring the flag, forwarding the per-pair sample
count, dropping the collision mask, and failing to NaN blocked pairs.

### Validated in play — three sessions, 866 decisions

M163's rule: timing replicates in one run, defect counts do not. All
three, with the flop, turn and river medians reproducing to the
hundredth:

| | before M176 | after |
|---|---|---|
| flop median | 6.71s | **1.51 / 1.59 / 1.54s** |
| turn median | 0.87s | **0.15s** in all three |
| river median | 4.66s | **0.12 / 0.12 / 0.11s** |
| within 2s | 61.6% | **99.3%** |
| within 5s | 77.3% | **100%** |
| worst decision | 10.09s | **2.22s** |
| defects / uniform rows | 0 | **0 / 0** |

**An effect that was not predicted, and it is the best kind.** The turn
and river got faster too, though M176 targeted the flop table. Those
streets solve on four- and five-card boards where `remaining_needed <= 1`
and BOTH builders enumerate rather than sample — and enumeration is
exactly the case the two agree on at 0.00e+00. So **turn and river
strategies are byte-identical to before, computed 5.8x and 39x faster**;
only flop values change at all.

Attribution, kept straight: flop 6.71 -> 1.51 and turn 0.87 -> 0.15 are
M176 alone. River 4.66 -> 0.12 is M174 (12.18 -> 0.65s) and then M176
(0.65 -> 0.12).

**The flop is no longer the slow street, and no street is.** The
ten-game benchmark opened with 11.8% of decisions over 8s; there are now
none over 2.22s.

## M177 — the river measured: certification refused, and the flop's rule inverts

Benchmark recommendation 1. The river had the lowest measured error of
any street (0.0626, M174) and 29% of postflop advice, all of it disclosed
as "accuracy on this street has not been measured". The turn was tested
and genuinely failed (M175); the river had simply never been asked.

**The rule was fixed before the data**: certify only if ZERO spots at
percentile >= 0.75 exceed 0.10 aggression error — the bar the flop
cleared (9 spots, worst 0.0571) — at BOTH node types, with fold error
also under 0.10.

56 spots from real play, four cells, each scored against a full-range
169-class reference WITH a real size menu, solved twice:

| cell | n | mean | worst | over .10 | fold worst |
|---|---|---|---|---|---|
| opening / strong | 14 | 0.1812 | 0.5082 | **6** | 0.0000 |
| opening / weak | 14 | 0.0425 | 0.4971 | 1 | 0.0000 |
| facing / strong | 14 | **0.3411** | 0.9993 | **8** | 0.5665 |
| facing / weak | 14 | 0.0555 | 0.2864 | 2 | 0.5622 |

**REFUSED**: 14 of 28 strong-band spots over 0.10.

### The flop's threshold points the WRONG WAY on the river

Error concentrates in the band a certificate would vouch for — strong
hands fail **50%** of the time against the weak band's **11%** — and the
mechanism is consistent in every worst case: **the river over-commits
with strong-but-not-nutted hands.**

    Kc8s  pct 0.921  facing   commits 1.0000   reference checks 0.995
    8hJs  pct 0.895  facing   shoves  0.9707   reference checks 0.9988
    KhTc  pct 0.835  facing   commits 1.0000   reference checks 0.9995
    Qc5d  pct 0.780  facing   folds   0.1464   reference folds  0.7130

So `UNMEASURED_STREET_NOTE` is now false for the river in two ways: the
street HAS been measured, and hand strength DOES predict there — in the
opposite direction to the flop. `RIVER_MEASURED_NOTE` replaces it and
names which advice to distrust, which is the half a player can act on.
The turn keeps the old note: M175 measured its strength/error
correlation at **+0.057**, so the two streets are uncertified for
genuinely different reasons and must not share a sentence.

### Facing a bet had never been measured on any street, and it is worse

**All 368 river spots across ten benchmark sessions are opening
decisions** — the harness does not merely fail to RECORD facing-a-bet
nodes, it never produces them. They were constructed here. Those cells
are the worse ones on both bands (0.3411 against 0.1812 strong, 0.0555
against 0.0425 weak), and F38 — the most severe defect ever found in this
engine — is what that blind spot cost the last time.

### Held to an evidence bar this project has failed twice before

M168 claimed a strength/error inversion for the turn from **4** spots per
band; M175 withdrew it at 12. This was run at 8 per cell, seen, and
extended to **14 across both node types** before any of it went into a
user-facing note — where the gap grew rather than evaporated (facing/
strong 0.2416 -> 0.3411 while facing/weak stayed at 0.0585 -> 0.0555).
Stated as measured over 56 spots, not as a law.

### A reference built at the wrong pot, caught before it produced numbers

The first version built the reference tree with the node's POST-BET pot
as its starting pot and then bet again, offering `raise:87.50` where
production offers `raise:25.00` — scoring hero-facing-a-43.75bb-bet
against hero-facing-a-12.5bb-bet and calling the difference error. The
tree sizes bets off the pot it is built with, so a facing-a-bet reference
must start from the street's OPENING pot and walk down. Caught by
checking the two action menus matched before running anything.

Four mutations caught: the river falling back to the unmeasured note,
certifying the river anyway, dropping the direction from the note, and
thinning the evidence back to M168's 4 per cell.

## M178 — the turn cache ceiling was stale, not wrong

Found incidentally while measuring what a wider turn range would cost.

`_turn_path_cache` sat at **maxsize 14**. That number was derived twice,
correctly both times: M127 set 20 when a turn entry measured 7.95 MB, and
M170 lowered it to 14 when giving the turn a sized re-raise grew entries
to 11.01 MB (20 of those being 220 MB against a 168 MB budget).

**Then M173 replaced the chained three-street solve with a standalone
one-street solve, and nobody re-derived it.** A turn entry now measures
**0.22 MB** — 50x smaller — so 14 entries occupied **3 MB of a 168 MB
budget** while turn requests missed the cache.

Raised to **192**, sized against the change most likely to land next
rather than against the maximum the budget allows (~772):

| turn range cap | entry | 192 entries | |
|---|---|---|---|
| 26 (shipped) | 0.22 MB | 42 MB | |
| 60 | 0.45 MB | 86 MB | |
| 100 | 0.77 MB | 148 MB | still valid |
| 140 | 1.07 MB | 205 MB | would fail, deliberately |

So adopting a wider turn cap up to 100 keeps this valid, and 140 forces a
deliberate re-derivation instead of a silent overrun.

### The safety net had a one-sided hole

`test_cache_ceilings_are_sized_against_what_an_entry_actually_costs`
(M127) catches a ceiling that is too HIGH — it measures a real entry and
asserts the budget. It cannot catch one left far too LOW, because nothing
overruns. **That is exactly how 14 survived M173**: the entry got 50x
cheaper, no test had anything to say, and the cache quietly stopped
being useful.

`test_the_turn_cache_ceiling_matches_what_a_turn_entry_now_costs`
asserts the other direction — the ceiling must be within a tenth of what
the budget affords for a real measured entry. Both mutations caught:
reverting to 14, and raising past the budget.

No accuracy tradeoff and no latency cost; the cache simply holds a
session's worth of turn boards instead of fourteen.

## M179 — flop-level coverage for the turn: better advice, still uncertifiable

Benchmark recommendation 2. The turn is 29% of postflop advice and the
one street measured and REFUSED certification (M175). It ran at cap 26
while the flop ran at 100 — and M173's frontier, which chose 26, tested
4/14/26/44/60 and **never tested the regime that fixed the flop** (M172:
error ROSE from 26 to 60, then HALVED at 100). The latency argument that
closed that door was gone: a turn solve was 1.67s when 26 was chosen and
0.09s after M176.

56 spots from real play, **four cells** — {opening, facing a bet} x
{strong, weak} — each against a full-range 169-class reference built at
the street's own OPENING pot and solved twice.

| cap | aggression | fold | worst fold | solve (production) |
|---|---|---|---|---|
| 26 (was shipped) | 0.1721 | 0.1034 | 0.9608 | 0.09s |
| 60 | 0.1587 | 0.0773 | 0.9628 | 0.16s |
| 100 | 0.1281 | 0.0552 | 0.7591 | 0.31s |
| **140 (adopted)** | **0.0999** | **0.0309** | **0.5897** | 0.68s |

### The primary answer is NO, and it retires a hypothesis

**Coverage does not make the turn certifiable.** 12 to 14 of 28
strong-band spots exceed 0.10 at every cap tested, non-monotone and never
close to zero. The turn's refusal is **structural**, not a coverage
budget — which retires the hypothesis this project's own benchmark report
proposed ("the turn needs the flop's coverage, not merely more than it
had"). Pinned by `test_the_turn_is_still_refused_certification_at_every_
measured_cap`.

### Adopted on SEPARABILITY, not on the mean

Paired against cap 26 over 56 spots, only 140 clears two standard errors
on aggression: **-0.0722 +/- 0.0296 (2.4 sigma), 33 spots better / 19
worse**. **Cap 100's gain does NOT** (-0.0440 +/- 0.0280, 1.6 sigma)
despite being the flop's own setting at a third of the cost — M141 and
M166 were each nearly adopted on exactly that kind of non-separable mean,
so it was left.

**The FOLD axis is what earns the cost, and every step is separable
there**: cap 26 -> 140 is **-0.0725 +/- 0.0253 (2.9 sigma) with 17 spots
better and 2 worse**, worst case 0.9608 -> 0.5897. That is F38's axis; a
fold error near 1.0 means the advice folds where a fuller solve calls,
essentially always.

### Stability checked, not assumed

At n=28 the four cap means were 0.1633 / 0.1429 / 0.1239 / 0.1036; at
n=56 they are 0.1721 / 0.1587 / 0.1281 / 0.0999. Ordering and magnitudes
held. **That is exactly what M166's split failed to do when its sample
grew**, and why the extension was run rather than adopting at n=28 where
nothing was separable at all.

### The river's inversion does NOT generalise to the turn

Facing-a-bet is only 1.1x worse than opening for strong turn hands and
BETTER for weak ones (0.5x), where the river was 1.9x worse (M177). So
M175's opening-only turn figures understate it only slightly. Worth
having checked: carrying the river's shape across would have been M110's
over-generalisation again.

### M178's mechanism fired exactly as designed

M178 set the turn cache ceiling to 192, sized so a cap up to 100 stayed
valid and 140 would "force a deliberate re-derivation rather than a
silent overrun". Adopting 140 did exactly that. The real entry measures
**1.33 MB** — not the 1.07 MB an isolated solve suggested, because the
cached object carries more than the solve's arrays, which is why M127's
rule is to measure a REAL entry — so the ceiling re-derived to **96**
(128 MB of a 168 MB budget).

Four mutations caught: reverting the cap to 26, adopting the
non-separable cap 100, certifying the turn anyway, and leaving the cache
ceiling over budget.

### Validated in play, and the latency cost is fully absorbed

Three sessions, 813 decisions:

| | before M179 | after (131 / 132 / 133) |
|---|---|---|
| turn median | 0.16s | **0.84 / 0.80 / 0.79s** |
| overall median | 0.12s | **0.11s** |
| p90 | 1.63s | **1.55s** |
| worst decision | 3.43s | **1.98s** |
| within 5s | 100% | **100%** |

The turn got 5x slower exactly as the isolated solve predicted (0.09s ->
0.68s) and **no percentile a user experiences moved** — at 0.84s the turn
is still well below the flop's 1.51s, so it never touches the p90 or the
max, both of which are set by the flop and unchanged.

### One defect, and it is NOT this milestone's

Session 132 flagged `trash (84o) folds 0.002 facing action` — 3-handed,
BB facing an SB raise at 50bb. Causation was tested rather than argued:
the same request returns a **byte-identical** row at cap 140 and cap 26,
so the turn cap has no effect on it.

It is the multiway preflop fold/play boundary, which M98 explained (every
terminal priced at raw showdown equity, so PLAYING is uniformly
underpriced and the fold/play boundary cannot move correctly) and
M110/M111 measured. Blocked on continuation values keyed by range
strength — the item the benchmark report recommends leaving alone.

Worth stating the severity accurately rather than by the flag's wording:
BB closing the action needs ~28% equity and 84o has roughly 33%, so
CALLING is defensible; what is wrong is that it is a **pure 0.998 call
with no mixing**, which is M74's bang-bang behaviour. One occurrence in
~5,000 decisions, on a response that already carries
`sizing_confidence: low`.

**It had never appeared in 4,481 prior benchmark and validation
decisions.** It surfaced because seeds 131-133 had never been run —
which is the case for running new seeds rather than re-running known-good
ones, and the reason defect counts get three sessions instead of one.

## M180 — the flop cap holds, and the flop's certification does not

Benchmark recommendation 3: revisit the flop cap now that latency is not
the constraint. M172 raised it 26 -> 100 and measured cap **140 as more
accurate** (0.0956 against 0.1065), rejecting it on cost at 6.63s. After
M176 a flop reference costs 5.74s where M172 paid ~65s, so the cost
argument was gone.

### The cap stays at 100 — M172's cap-140 result does not replicate

56 spots, four cells ({opening, facing a bet} x {strong, weak}), against
a full-range 169-class reference at s200/i2500 built at each request's
own pot and stack:

| cap | ALL | op/str | op/weak | fa/str | fa/weak | over .10 | solve |
|---|---|---|---|---|---|---|---|
| **100 (shipped)** | 0.0693 | 0.1480 | 0.0756 | 0.0528 | 0.0006 | 8 | 1.13s |
| 140 | 0.0468 | 0.0976 | 0.0561 | 0.0322 | 0.0013 | 9 | 1.62s |
| 169 | 0.0458 | 0.1217 | 0.0051 | 0.0560 | 0.0005 | 9 | 2.03s |

Nothing is separable — cap 100 -> 140 is 0.97 sigma on aggression and
1.85 on fold — and on aggression the wider caps make **more spots worse
(28) than better (17)** while the mean improves, which is a few large
gains masking many small regressions. The over-0.10 count RISES, 8 -> 9.

**Coverage beyond 100 buys nothing**: uncapped at production precision is
0.0458 against cap 140's 0.0468. The flop's residual error is
precision-bound, not coverage-bound.

### What the study actually found: the certificate fails

The flop was the only street carrying a reliability guarantee. M167
granted it on **9 spots** at percentile >= 0.75 — mean 0.0144, worst
0.0571, **zero over 0.10** — measured at **cap 26 with 500 iterations**.
M172 changed both and **it was never re-run**.

| | M167 granted on | measured now |
|---|---|---|
| spots | 9 | **28** |
| mean | 0.0144 | **0.1004** |
| worst | 0.0571 | **0.9535** |
| over 0.10 | **0** | **6** |

**Not coverage**: the strong band fails at cap 100, 140 and 169
(uncapped) — 6, 7, 9 spots over 0.10. **Not precision**: holding the cap
and varying iterations 250 / 500 / 2500 gives 6, 6, 5 — flat, consistent
with M152's finding that precision is a dead axis here. **No threshold
rescues it**: the failures include percentiles 0.913 and 0.978.

The worst case is what a certificate should never cover: **Kc8c on
7h9hKd, percentile 0.913 — top pair on a two-flush board. The reference
bets 0.9987; the product bets 0.045.** Its reference drifted 0.0003
between seeds, so it is not a reference artifact.

`CERTIFY_RELIABILITY_ON_STREETS = ()`. **No street is certified.**

### The flop gets its own note, and it names no direction

`UNMEASURED_STREET_NOTE` says accuracy "has not been measured against a
larger solve the way the flop has" — self-contradictory on the flop,
which has been measured more than any other street.

`FLOP_MEASURED_NOTE` says it was measured and refused, and **deliberately
claims no direction**: at 56 spots strong-vs-weak is **1.32 sigma** and
opening-vs-facing is **1.83 sigma**, neither separable. M166 asserted
exactly this kind of split from a smaller sample and M167 withdrew it.
The river's note DOES name a direction because the river's split is
separable; pinned by
`test_every_street_note_is_distinct_and_matches_its_evidence`.

### The dormant machinery is kept and still tested

The two-band mechanism now fires for no street. Rather than delete it
(restoring a certificate is a stated possibility) or leave it untested
(dormant code rots), one test certifies the flop by monkeypatch for its
own duration and checks both bands still split at the threshold.

### Two process notes

**A methodological error caught in my own follow-up**: the first
precision test hardcoded `effective_stack_bb=97.0` where the references
had been built at each spot's real stack (47.5bb for one). It produced a
wrong "iterations are the cause" reading — i250/i500/i2500 as
0.1649/0.1091/0.1005, suggesting the M172 iteration cut was the
regression. Re-run with matched pot AND stack it is 0.1004/0.0924/0.1003,
flat, and the conclusion inverts.

**This is the FOURTH claim in this project overturned by measuring more
of the same thing** — M166 (27 -> 45 spots), M168 (4 -> 12 per band),
M110's positional read, and now M167's certificate at 9 -> 28.

## M181 — the benchmark harness could not produce a facing-a-bet node

Benchmark recommendation 4, and the gap is worse than the report stated.
It said the harness does not RECORD facing-a-bet on the turn and river.
In fact it never GENERATED those nodes at all: **all 368 river spots
across ten benchmark sessions were opening decisions.**

The `facing_bet` flag was never wrong — it is `"fold" in strategy`, which
is exactly right, since folding is not a legal action at a street's
opening decision. There was simply nothing to flag.

The cause, in the session driver: the flop already drew a randomised
action path (`rng.choice([[], [], ["raise"]])`, hence the 33% facing-a-bet
rate seen on that street), while the turn and river **hardcoded
checked-through paths and passed no action path of their own**. Both now
draw the same way.

Measured on a 40-hand probe after the fix:

| street | facing a bet |
|---|---|
| flop | 5/19 (26.3%) |
| **turn** | **4/15 (26.7%)** |
| **river** | **6/13 (46.2%)** |

Zero defects, zero uniform rows.

### Why this mattered more than a missing field

**F38 lives on this axis.** With nine-high facing a bet the product
recommended shoving 97.5bb 0.567 of the time where the correct play is to
fold 0.987 — and it went unfound for many milestones because folding is
not a legal action at an opening decision, so no amount of measuring
there could ever see it. M177 then measured the river properly and found
facing-a-bet cells up to **6x worse** than opening ones on the same
street (fa/weak 0.0555 against op/weak 0.0425; fa/strong 0.3411 against
op/strong 0.1812).

**Two whole benchmarks ran inside that blind spot** — the ten-game
original and its re-run — and both reported postflop coverage that
excluded the harder half of every later street.

### Pinned in the repo, because the harness is not

The session driver lives in scratch tooling, so the durable half is a
test: `test_a_facing_a_bet_node_is_answerable_on_every_postflop_street`
asserts such a node is reachable and answered on all three streets AND
that it actually offers `fold` — which is what makes it a different
question from the opening decision.

The second guard is the wrinkle that makes the harness fix non-trivial:
**a turn path handed to a RIVER request must CLOSE the turn's betting.**
A bare `["raise"]` leaves it open and the API correctly refuses to deal a
river card. Reusing a street's own path downstream — the obvious way to
add this coverage — turns river coverage silently into 422s.

Both tests restore a real `FLOP_TURN_RAISE_SIZES` by monkeypatch: the
suite fixture zeroes it for speed, so under the suite the turn and river
offer only check and all-in and a facing-a-BET node cannot exist. That is
M143's trap exactly, and its own guard does the same thing.

Two mutations caught: the standalone river ignoring `river_action_path`
(collapsing every river request back to the opening decision), and the
turn accepting an unclosed street before dealing a river card.

## M182 — pricing advice in chips, and what it says about every number before it

Every accuracy figure in this project is a FREQUENCY distance. That is a
convergence measure and a poor quality measure, for a reason worth
stating plainly: **a frequency gap costs nothing when the actions it
splits between are worth the same** — and solvers mix precisely when
actions are near-indifferent, so large frequency errors live exactly
where they are cheapest.

`poker_solver/ev.py` prices the difference instead: `EV(reference row) -
EV(shipped row)`, with everything else held identical — same opponent
strategy, same continuation below the node, same range. Only hero's mix
at the one decision changes.

### Applied to M180's certification failures, it inverts them

The 28 strong-band flop spots M180 withdrew the certificate on:

| group | n | mean loss | worst | mean value spread |
|---|---|---|---|---|
| **failed** the 0.10 frequency test | 11 | **0.0424 bb** | 0.1655 | 1.71 |
| **passed** it | 17 | **0.5351 bb** | **3.1608** | 10.29 |

**The spots that passed cost 12x more than the ones that failed.**

    correlation(aggression error, EV loss) = -0.248
    correlation(TVD, EV loss)              = +0.459

The aggression metric is not merely imperfect — on this set it is
**anti-correlated** with what the advice costs. The five costliest spots
have aggression error 0.0001-0.0055 and TVD 0.26-0.72: they match on
TOTAL aggression while differing on WHICH SIZE, at nodes where actions
are worth 5-14bb apart.

    6c7h on 6dKdKh  aggr_err 0.0055  tvd 0.4637  LOSS 3.16 bb
    3hTc on AhAsTd  aggr_err 0.0001  tvd 0.2554  LOSS 0.97 bb

So EV loss is roughly **frequency difference x value spread**, and
aggression error drops the spread entirely.

### What this does and does not change

**M180's withdrawal stands, and is stronger than it was argued.** The
whole strong band means **0.3415 bb** of loss with a worst of 3.16 bb;
that is expensive advice whichever spots the frequency test flagged. What
is now clear is that the test was selecting the wrong ones.

**It does not retroactively invalidate M177 or M179.** Both compared arms
on the SAME spots with the same metric, so their paired comparisons
remain valid as convergence measurements. What they cannot claim is that
those differences were worth what they implied in chips — and M179's
adoption of turn cap 140 rested substantially on the FOLD axis, a
composition measure rather than a total, so it sits closer to TVD than to
aggression.

### Limitations, stated rather than buried

The opponent's range is **uniform** over the reference's hands, not the
derived range a real request would face; the opponent plays the reference
strategy; the stack is fixed at 97bb; 28 spots. These bound how far the
absolute bb figures generalise. The RANKING between metrics — which is
the finding — is robust to all of them, since every spot is scored the
same way.

### A control that corrected the module, not the code

The first test asserted a 0% equity hand prefers checking to betting. It
failed: against a UNIFORM opponent both price at exactly -5.0, because a
uniform opponent folds to the all-in half the time and that fold equity
exactly offsets having no showdown equity. The code was right and the
test's intuition was wrong; bluffing is only unprofitable when nobody
folds, so the control now uses an opponent who never folds.

## M183 — the accuracy studies re-run in chips, and a correction to M182

M182 built the EV-loss primitive. This re-runs the accuracy question with
it across all three streets and both node types, structured around the
goal that matters: **helping a player win over time**, which makes the
unit bb per decision and requires weighting spots by how often they
actually occur.

Two corrections over M182's first pass, both material:

* **The opponent's reach is the reference's own range weights**, not a
  uniform vector. Pricing against an opponent holding every hand equally
  overstates bluff-catches and understates value.
* **Pot and stack come from `/advise` per spot**, so the tree being
  priced is the one a player faces. That is the error M182's first pass
  made — and the second time this session a hardcoded stack changed a
  conclusion.

### What the advice costs

48 spots, 8 per cell across {flop, turn, river} x {opening, facing a bet}:

| cell | n | mean | median | worst | spread |
|---|---|---|---|---|---|
| flop / opening | 8 | 0.0294 | 0.0163 | 0.0837 | 1.74 |
| **flop / facing** | 8 | **0.3823** | -0.0021 | **1.3779** | 7.03 |
| turn / opening | 8 | 0.0224 | 0.0069 | 0.1193 | 1.52 |
| turn / facing | 8 | -0.0431 | -0.0003 | 0.2896 | 9.19 |
| river / opening | 8 | 0.1190 | 0.0654 | 0.3658 | 1.45 |
| river / facing | 8 | -0.1817 | 0.0027 | 0.0743 | 10.06 |

Weighted by observed occurrence (street mix from the ten-game benchmark,
facing-a-bet share from the post-M181 sessions):

    COST OF THE ADVICE: 0.0472 bb per postflop decision
                      ~ 4.7 bb per 100 postflop decisions
                      ~ 6.6 bb per 100 hands (1.39 postflop decisions/hand)

### The distribution matters more than the mean

| | |
|---|---|
| median loss | **+0.0071 bb** — effectively free |
| spots costing <= 0.01 bb | **27/48 = 56%** |
| spots costing > 0.10 bb | 10/48 = 21% |
| top 5 spots as a share of all loss | **79%** |

So the advice is **mostly free and occasionally expensive**, which is a
far more actionable shape than any mean error this project has reported.
Improving the average is the wrong target; finding the tail is the right
one.

### Where the money is, and what predicts it

**Facing-a-bet nodes carry 85% of all loss** while being half the spots —
mean |loss| 0.3107 bb against 0.0569 at opening decisions, a 5.5x gap.
That is precisely the axis no study could see before M177 and the harness
could not even generate before M181.

| predictor of \|loss\| | correlation |
|---|---|
| aggression error | +0.141 |
| value spread alone | +0.209 |
| TVD | +0.562 |
| **TVD x value spread** | **+0.772** |

12 of 48 spots have `TVD x spread > 1.0` and carry **82% of all loss**.

### CORRECTION to M182

M182 reported `correlation(aggression error, EV loss) = -0.248` and
called the metric **anti-correlated** with cost. **That does not
replicate.** Under this setup it is **+0.232** over all 48 spots and
**+0.140** on the flop alone. M182's negative sign was an artifact of its
narrower configuration — flop strong band only, uniform opponent reach,
fixed 97bb stack.

What survives both measurements, and what M182 should have said: **frequency
distance is a WEAK predictor of EV loss** — |r| <= 0.46 in every
configuration tried — not a reliably inverted one. The reason stands
unchanged: EV loss is approximately frequency-difference x value-spread,
and a frequency metric drops the spread.

M182's conclusions that do NOT depend on the sign are unaffected: M180's
withdrawal still stands, and pricing in chips still reveals costs that
frequency distance cannot rank.

### What this suggests next, stated but not built

`TVD x spread` needs a reference and so cannot be computed live — but
**value spread alone can be**, from the shipped solve, and facing-a-bet
is known from the request. A runtime signal along "this decision has a
lot at stake and is the kind we are least accurate on" is therefore
cheap and would target the 15% of decisions carrying 85% of the cost.
Not built here: it needs its own measurement showing the runtime-visible
half predicts well enough to be worth surfacing.

## M184 — the product aim, written down where it governs

CLAUDE.md now opens with what the product is FOR, before what it is:

> **The aim is to help a player make money.** Not to be close to GTO, not
> to converge, not to answer quickly — those are means.

This is not decoration. Placed above `## Current state`, it decides what
counts as evidence for every claim below it, and this session showed
repeatedly that the default answer was wrong:

* **The unit is bb per hand, not distance from a reference.** M182/M183
  measured that a frequency error costs nothing when the actions it
  splits between are worth the same — and solvers mix precisely when
  actions are near-indifferent, so the largest frequency errors live
  where they are cheapest.
* **Weight by occurrence.** A balanced spot set is unrepresentative by
  construction; every per-hand claim has to be re-weighted before it
  means anything.
* **The tail is the target, not the average.** Median decision costs
  0.0071 bb, 56% cost under 0.01 bb, and the five worst carry 79% of all
  loss. Optimising the mean — which is what every accuracy study in this
  project did until M183 — is close to worthless.
* **Honesty is part of the product.** A player who cannot tell which
  advice to trust cannot use it to make money, which is why the
  confidence signals exist and why no street currently claims a
  certificate (M180).

Guarded by `test_the_product_aim_is_stated_and_its_measure_still_exists`,
which is deliberately light: it does not police the prose. It checks the
section exists, that it names expected value rather than a frequency
distance as the measure, and that `poker_solver.ev` still provides the
primitives it points at — because a governing definition referencing a
deleted module is worse than none. Both mutations caught: deleting the
section, and reverting the unit to a frequency distance.

## M185 — the runtime signal: coarse, real, and the only one that exists

M183 recommended a runtime signal targeting the 15% of decisions carrying
85% of the cost, and said it needed its own measurement first. It got
one, and the measurement changed what got built.

### The fine-grained signal does not exist

`TVD x value spread` predicts |EV loss| at **+0.772**, but TVD needs a
reference solve and cannot be computed live. Every runtime-visible
feature was measured on the same 48 spots:

| runtime feature | corr with \|loss\| |
|---|---|
| action count | +0.237 |
| entropy of the recommendation | +0.215 |
| max probability | -0.141 |
| hand strength | +0.112 |
| **value spread (shipped solve)** | **+0.071** |
| all-in mass | -0.005 |

Value spread was the obvious candidate and **adds nothing**: every
spread-threshold rule flags the same decisions as "facing a bet", because
facing-a-bet nodes ARE the high-spread ones (mean 8.76 against 1.57), and
WITHIN facing nodes the correlation is **-0.109**. The action count, the
best of them, is itself a proxy for the same split — 4 actions with fold
against 3 without.

### The coarse signal is real and separable

| node type | n | mean \|loss\| | median |
|---|---|---|---|
| facing a bet | 24 | **0.3107 bb** | 0.0235 |
| acting first | 24 | 0.0569 bb | 0.0126 |

**5.5x**, delta +0.2538 +/- 0.0983 (**2.58 sigma**), permutation
**p = 0.0054** over 20,000 shuffles — used because the distribution is
heavy-tailed enough that the t-style error bar is not trustworthy alone.
It holds on every street: flop 19.5x, turn 6.7x, river 1.8x. These
decisions are half of all postflop advice and carry **85% of the cost**.

`FACING_A_BET_COST_NOTE` fires on them, and is **derived from the rows**
— folding is legal only when facing a bet — so it survives changes to
path shapes and size menus, the same reason M144 built the sizing note
that way. Reading it off `flop_action_path` would look equivalent and is
not: the request says what was ASKED, the rows say what the tree
OFFERED.

### What the note deliberately does not say

The MEDIAN facing-a-bet decision costs **0.0235 bb** — nearly as cheap as
an opening one. It is the tail that differs. So the note says the cost
concentrates here and that "most individual answers here are still
accurate", rather than implying this particular answer is likely wrong.
A signal that overstates on half of all decisions is one a player learns
to ignore, which is the failure M167 recorded and the reason a
finer-grained rule was looked for first.

Four mutations caught: the note never firing, firing on every decision,
being read from the request instead of the rows, and dropping the hedge.

## M186 — the cost measured at 144 spots, and 20 sessions to weight it

M183 priced 48 spots and put the cost at 8.6 bb/100 hands with a 2-sem
range of **-2.1 to +19.3** — an interval spanning zero, which is not an
answer. M185 then shipped a user-facing note built on those figures.
This tripled the sample and re-weighted it against 20 fresh sessions.

### The estimate doubled and became real

| | 48 spots | **144 spots** |
|---|---|---|
| cost per postflop decision | +0.0696 bb | **+0.1400 bb** |
| per 100 hands | +8.6 | **+17.2** |
| 95% interval | -2.1 to +19.3 | **+4.5 to +29.9** |

**Why it moved**: at 8 spots per cell, three of six cells had NEGATIVE
mean cost (turn/facing -0.0431, river/facing -0.1817). At 24 they are
+0.1834 and +0.3565. The negatives were noise, and they had been
suppressing the total. This is the fourth time in this project a
conclusion moved substantially when the sample grew, so 144 is the
current best estimate and not a settled one.

### The shape is the finding

| | |
|---|---|
| median decision | **+0.0034 bb** |
| costing <= 0.01 bb | **52%** |
| costing > 1 bb | **8%** |
| top 10 of 144 spots | **78% of all loss** |

The product is almost always right and occasionally very expensive.
**Every accuracy study before this optimised mean frequency error**,
which on this evidence is close to the wrong objective — the average
decision is already free.

### Facing a bet: stronger than M185 shipped

| | M185 (n=48) | **M186 (n=144)** |
|---|---|---|
| mean cost, facing | 0.3107 bb | **0.5168 bb** |
| mean cost, opening | 0.0569 bb | 0.0348 bb |
| ratio | 5.5x | **14.8x** |
| share of all cost | 85% | **94%** |
| separability | 2.58 sigma | **3.06 sigma** |

The split got STRONGER with more data. `FACING_A_BET_COST_NOTE` is
corrected accordingly, and now also states that 8% of decisions cost more
than a big blind and nearly all of those are here — the concrete fact
behind the warning.

### 20 sessions: speed settled, and the real cell mix

5,352 decisions over 2,400 hands. Median **0.11s**, p90 1.56s, worst
**2.17s**, 99.7% inside two seconds. **Zero defects and zero uniform rows
in all twenty sessions**; per-session medians varied 0.09-0.12s.

The sessions also supply the weighting, and it differs from what M183
assumed: facing-a-bet is more common on the flop and turn (32.3% / 33.7%
against 26.3% / 26.7%) and much rarer on the river (31.8% against 46.2%).
Since facing-a-bet carries 94% of the cost, using the observed mix rather
than the assumed one matters.

The harness now records an `ev_cell` per decision — street x node type —
because it previously recorded latency, defects and confidence and
NOTHING about money, so a session could report "clean" while saying
nothing about whether the advice helps a player win.

## M187 — 402 spots: the estimate settles, the tail does not

M186 put the cost at 17.2 bb/100 from 144 spots and recommended pushing
to 300-400 before using the level to compare configurations. This is that
run: 67 spots per cell, drawn from a pool three times larger (all 30
sessions rather than the first ten).

### The level has stabilised

| n | bb/100 hands | 95% interval |
|---|---|---|
| 48 | 8.56 | -2.12 to +19.25 |
| 144 | 17.22 | +4.48 to +29.95 |
| **402** | **15.26** | **+4.73 to +25.79** |

48 -> 144 doubled, and that was a noise correction (three cells had
negative means at 8 spots each). **144 -> 402 is stable** — each estimate
sits comfortably inside the other's interval. The level is now as
settled as this method will make it.

### The interval barely tightened, and that is the finding

Tripling the sample moved the half-width only 12.7 -> 10.5 bb/100,
because **more spots kept finding more extreme ones**:

    worst single decision:  n=48  4.96 bb   n=144  8.17 bb   n=402  11.45 bb

The distribution is heavy-tailed enough that the mean converges slowly.
Concentration at 402 spots:

| | share of decisions | share of all cost |
|---|---|---|
| top 5 | 1% | **29%** |
| top 10 | 2% | **46%** |
| top 20 | 5% | **67%** |
| top 40 | 10% | **85%** |

Median decision still costs **+0.0028 bb**, 52% cost under 0.01 bb, and
9% cost over 1 bb with 2% over 5 bb.

### Where the remaining uncertainty lives — and it is not everywhere

| cell | n=24 | n=67 | sem at n=67 |
|---|---|---|---|
| flop / opening | +0.0243 | +0.0145 | **0.0034** |
| turn / opening | +0.0150 | +0.0148 | **0.0032** |
| river / opening | +0.0649 | +0.0663 | **0.0161** |
| flop / facing | +0.5046 | +0.3614 | 0.2518 |
| turn / facing | +0.1834 | +0.0760 | 0.1100 |
| river / facing | +0.3565 | +0.6127 | 0.2560 |

**The three opening cells are settled** — stable between samples and with
error bars an order of magnitude smaller. They are also nearly free, and
together contribute 0.0181 of the 0.1241 total. **The three facing cells
carry 85% of the weighted cost and essentially all of the uncertainty**:
their sems alone reproduce the total sem to three decimals.

So further sampling should go to **facing-a-bet cells only**. The opening
cells can stop being sampled. Halving the interval needs roughly 4x the
facing spots — about 800 — which is ~3.5 hours at current solve speeds.

### Facing a bet: the ratio keeps growing, so it is quoted as a floor

| n | ratio | share of cost | separability |
|---|---|---|---|
| 48 | 5.5x | 85% | 2.58 sigma |
| 144 | 14.8x | 94% | 3.06 sigma |
| **402** | **21.9x** | **96%** | **5.64 sigma** |

0.6983 bb facing against 0.0320 acting first. A ratio that rises
monotonically with the sample is one whose extreme spots are still being
discovered, so `FACING_A_BET_COST_NOTE` now says **"at least 20 times"**
rather than quoting 21.9 — deliberately understating a figure that is a
floor rather than a point estimate.

## M188 — 801 facing-a-bet spots: the interval closes, the tail does not

M187 showed the three opening cells were settled and nearly free while
the three facing cells carried 85% of the cost and essentially all the
uncertainty, and recommended sampling facing cells only. This is that
run: **267 spots per facing cell, 801 total**, no compute spent on
openings (their means are reused).

### The interval closed as predicted

| | n=402 (all cells) | **n=801 facing** |
|---|---|---|
| cost | 15.26 bb/100 | **16.06 bb/100** |
| 95% interval | +4.73 to +25.79 | **+8.42 to +23.69** |
| half-width | 10.53 | **7.63** |

Targeting the uncertain cells worked: 4x the facing spots cut the
half-width by 28% while the level held (15.3 -> 16.1, well inside both
intervals). **The cost is now established well clear of zero** — the
lower bound is 8.4 bb/100.

### Two corrections to what M187 shipped

**86%, not 96%.** M187's note said facing-a-bet carries 96% of the cost.
That was the share of raw |loss| within a **balanced 50/50 spot set**,
which is not what a player meets. Weighted by how often each decision
type actually occurs it is **86%**. The unweighted figure flattered it,
and the note is corrected.

Weighted contributions, which is the useful view:

| cell | share of play | mean | contributes |
|---|---|---|---|
| **flop / facing** | 14.4% | +0.5352 | **59.2%** |
| **river / facing** | 7.5% | +0.4300 | **24.6%** |
| river / opening | 16.0% | +0.0663 | 8.1% |
| flop / opening | 30.3% | +0.0145 | 3.4% |
| turn / facing | 10.7% | +0.0295 | 2.4% |
| turn / opening | 21.1% | +0.0148 | 2.4% |

Two cells — flop and river facing a bet, together 22% of decisions —
carry **84%** of the cost. The turn is now cheap on both node types.

**The ratio is 27.4x** (7.17 sigma), up from 21.9x. Increments across the
four samples are +9.3, +7.1, +5.5 — decelerating but not converged, so
the note still says "at least 25 times", deliberately a floor.

### The tail is NOT exhausted, and that bounds what this method can do

The worst single decision found went **11.45 bb at 201 facing spots to
73.25 bb at 801** — a river call/fold in a 30bb pot. **Verified real
rather than assumed**: its action spread is 76.81, so the loss sits
inside what the decision can physically swing, and all 801 spots pass
that check (`|loss| <= spread`, zero violations).

But it means the mean rests on very few decisions:

    trim top 0%   mean |loss| 0.8754
    trim top 1%   mean |loss| 0.6633
    trim top 2%   mean |loss| 0.5853
    trim top 5%   mean |loss| 0.4122

**One spot in 267 moves its cell's mean by 0.27 bb.** The raw mean is the
right statistic for a long-run money question — rare catastrophic losses
count fully in an average — but the number rests on a handful of
decisions, and each larger sample has found a worse one. Treat 16 bb/100
as a well-established floor whose level is still tail-sensitive.

### What this makes the highest-value work

18% of facing-a-bet decisions cost more than a big blind and 5% cost more
than five. **Characterising those specifically** — rather than reducing
mean error anywhere — is now the only work with a measurable payoff, and
it is concentrated in two cells covering 22% of play.

## M189 — the costly band: 12% of decisions, 74% of the cost

M188 ended by naming the next step: characterise the expensive
facing-a-bet decisions rather than sample more of them, since sampling had
hit diminishing returns on the interval while still finding new extremes.
This is that characterisation, and it produced a much sharper signal than
M185's.

### The cost concentrates in a BAND, non-monotonically

Splitting M188's 801 facing-a-bet spots by hand strength:

| strength | % costing > 1bb | mean \|loss\| |
|---|---|---|
| 0.00-0.20 | 4.1% | 0.129 |
| 0.20-0.40 | 4.9% | 0.200 |
| 0.40-0.55 | 12.3% | 0.443 |
| **0.55-0.75** | **29.0%** | **1.376** |
| **0.75-0.90** | **43.8%** | **2.531** |
| 0.90-1.01 | 11.5% | 0.541 |

**Both the weakest and the strongest hands are cheap.** The money is in
the middle — the "is my top pair actually good?" decision, strong enough
to continue and not strong enough to be obvious. That is also the one a
human finds hardest, which is a coincidence worth noticing but not
over-reading.

### As a runtime signal it is twice as sharp as M185's

| rule | fires on | catches | lift |
|---|---|---|---|
| facing a bet (M185) | 32.6% of postflop | 93% of cost | 2.9x |
| **facing + strength 0.55-0.90** | **12.1%** | **74%** | **6.1x** |

Both conditions are needed and neither alone is the signal: an in-band
hand ACTING FIRST is cheap, and a facing-a-bet decision outside the band
is much cheaper than one inside it.

### Replicated before shipping, because this is exactly how M166 went wrong

A band found by inspecting the same data that suggested it is a fitted
band. Split-half on a random partition of the 801 spots:

    half A   in-band 1.874 (n=148)   out-of-band 0.267 (n=252)   7.0x
    half B   in-band 1.874 (n=149)   out-of-band 0.307 (n=252)   6.1x

The in-band mean is identical to three decimals across independent
halves. M166 asserted a strength/error split from 27 spots and M167
withdrew it when 18 more broke it; this is 801 spots and it survives
being cut in half.

### Graded, not replaced

`FACING_A_BET_COST_NOTE` still fires on every facing-a-bet decision,
because out-of-band ones average 0.28-0.31 bb against an opening
decision's 0.032 — cheaper, not cheap. `COSTLY_BAND_NOTE` adds the
sharper half on top, and tells the player plainly that this is where
their own judgement is most likely to beat the engine.

`test_the_band_is_narrow_enough_to_be_worth_reading` guards the WIDTH
rather than the prose: widening it to catch more cost trades away the
property that makes it worth surfacing at all, and should be deliberate.

Four mutations caught: the band note firing on every facing decision,
never firing, ignoring the facing-a-bet condition, and being widened to
the whole strength range.

## M190 — widening for money: 44% less cost, and a real latency price

The first change this session that makes the ADVICE better rather than
better-disclosed — and the first to reverse an earlier verdict because
that verdict used the wrong metric.

### Why this was available

Every configuration verdict in this engine was reached on pooled
FREQUENCY error: cap 140 rejected for the flop (M180, 0.97 sigma),
precision declared dead (M152/M180), eleven range-composition rules
killed (M130-M141, M166). M183 then measured frequency distance as a poor
proxy for cost, and M189 that 74% of the cost sits in 12% of decisions.
**Those verdicts were reached with a metric that does not track money,
over a population where 52% of decisions cost under 0.01 bb.**

### Re-scored on EV loss in the costly band

297 spots (facing a bet, strength 0.55-0.90) carrying 79% of all facing
cost:

| arm | mean \|loss\| | paired delta | sigma | separable |
|---|---|---|---|---|
| shipped | 1.8740 | — | — | — |
| **cap 140** | **1.0489** | **-0.8250 +/- 0.1871** | **4.41** | **YES** |
| iters 1000 | 1.9651 | +0.0911 +/- 0.0632 | 1.44 | no |

**44% less cost, better on 124 spots and worse on 62.** By street: flop
1.4987 -> 0.8098, **river 3.4573 -> 1.5845**, turn unchanged (already at
140 from M179 — a useful check that the harness does what it claims).

The river is the largest single win: its cap was **26**, in a cell
carrying 24.6% of all cost.

### The confound was controlled, not assumed

Cap 140 sits closer to the 169-class reference than cap 100 does, so it
could score better by resembling it rather than by being right. Two
checks:

1. **The `iters 1000` arm also moves toward the reference** (250 against
   its 1500) and gains **nothing** (1.44 sigma). A pure-similarity effect
   could not produce that.
2. **The reference is converged in iterations**: 1500 against 3000 moves
   it a mean of **0.0508 bb** with TVD 0.0084 — **2.7%** of the 1.87 bb
   effect being measured. M138 had established sample-stability; nobody
   had checked the axis one of the arms varies.

### The price, measured over three sessions and larger than predicted

| | before | after |
|---|---|---|
| median decision | 0.11s | **1.03s** |
| p90 | 1.56s | 3.36s |
| worst | 2.17s | 3.65s |
| within 2s | 99.7% | **77.8%** |
| within 5s | 100% | 100% |
| defects | 0 | **0** |

Flop 1.53 -> 3.19s, river 0.11 -> 1.04s. **The turn also moved (0.81 ->
1.35s) despite its cap being unchanged** — a second-order cost of the
widening: three cache ceilings had to be lowered to stay inside the byte
budget (`river_path` 256 -> 96, `canonical_warm_starts` 128 -> 96), so
more requests miss.

**Isolated measurement under-predicted the request for the fifth time
this session** — 1.54s isolated against 3.19s in production on the flop.

### The trade, stated plainly

Roughly **16 -> 11 bb/100 hands** for a median decision going 0.11s ->
1.03s, worst case still 3.65s and everything inside 5s. By the product
aim — helping a player make money, with speed a means — that is the right
side of the trade, and 5 bb/100 is a large edge in poker terms.

**It is also reversible in one constant per street**, and the two halves
are independent: `MAX_PATH_QUERY_CLASSES_PER_SIDE` back to 100 restores
the flop, `RIVER_STANDALONE_CLASSES_PER_SIDE` back to 26 the river. The
flop carries the larger cost reduction (59.2% of total cost, 46% cut);
the river the larger latency multiple (9.5x, from a very low base).

### Three ceilings from one constant

Widening ONE cap moved THREE cache ceilings, none visible from the config
change itself. M127's byte test caught all of them by measuring real
entries rather than trusting the comment beside the number — the third
consecutive milestone it has done so.

## M191 — the river cap sweep, and a correction to M190's latency

Asked whether the river should be pulled back from 140 to 26 to recover
speed. The answer is **no**, and getting there corrected M190's own
numbers.

### The accuracy curve is monotone — there is no free middle

97 river spots in the costly band:

| cap | mean \|loss\| | reduction | paired sigma | separable |
|---|---|---|---|---|
| 26 | 3.4573 | — | — | — |
| 60 | 2.7233 | 21% | 1.91 | no |
| 100 | 2.2234 | 36% | 2.97 | YES |
| **140** | **1.5845** | **54%** | **3.52** | **YES** |

Worst case falls 73.3 -> 35.3 and decisions over 1bb fall 54 -> 24 across
the same range. Cap 60 is not even separable from 26. **This is not the
flop's shape** — M172 found flop error RISING from 26 to 60 before
halving at 100, so the monotonicity here had to be measured rather than
assumed.

### M190's latency numbers were inflated by machine drift

M190's validation reported median 1.03s and flop 3.19s. Measured today,
**the same configuration gives median 0.47s and flop 2.04s.** Nothing in
the code changed between them.

This is M70's finding biting again — this machine has been observed
running identical work up to 1.7x slower across sessions — and M190
compared its validation sessions against a 20-session benchmark run
hours earlier. **That comparison was not sound and its latency table
overstates the cost of the change.**

Corrected, against the same 20-session benchmark but acknowledging the
cross-run caveat: flop 1.53 -> ~2.04s, median 0.11 -> ~0.47s. Still a
regression, roughly half what M190 reported.

### Paired, in one machine state, the cap barely matters

| | river 100 | river 140 |
|---|---|---|
| median | 0.46s | **0.47s** |
| p90 | 1.89s | 2.05s |
| worst | 2.16s | 3.72s |
| river street | 0.52s | **0.64s** |
| within 2s | 93% | 87% |

**Cap 140 costs 0.12s on the river and nothing on the median**, against
the isolated measurement's 0.23 -> 0.55s. Isolated numbers have now
over- and under-predicted the request in both directions this session;
only paired production measurement settles it.

So the river stays at **140**: it captures 54% of the available cost
reduction rather than cap 100's 36%, for a difference no user would
perceive.

### The general lesson, which cost two sessions to learn

**Any latency claim comparing runs from different machine states is
worthless here.** M190's own validation, run against an earlier
benchmark, produced a number large enough to prompt reverting a change
that measurement shows costs almost nothing. Latency comparisons must be
paired within one machine state — the same rule M70 set for solver
speedups, applied to end-to-end sessions.

### A test-isolation bug this surfaced

The suite went red on
`test_monte_carlo_equity_n_threads_rng_into_dealing_for_suit_diversity`,
which asserts `monte_carlo_equity_n` calls `deal_n_hands` exactly once.
It collected **171** calls.

**The flaw was in the test.** Monkeypatching a module attribute is
process-wide, so a background thread — an API prewarm left running by an
earlier test — put its own calls in the same list. The test passed
whenever the file ran alone, or when everything before it ran, or when
the whole suite was collected but only that test executed. It failed only
under the full run, where the prewarm was still going.

**M190 surfaced it without causing it**: wider range caps made prewarm
slow enough to still be running by the time this test executed. The
branch that exposed it changes only documentation — `api/` is
byte-identical to main — which is how the diagnosis started.

The spy now records the calling thread and counts only its own. Counting
a process-wide total was never what the test meant to assert.

## M192 — the second benchmark: the cost is no longer measurable

Twenty fresh sessions on the shipped configuration, a paired old-vs-new
speed comparison, and the cost re-measured from scratch rather than
extrapolated.

### The cost, measured rather than inferred

M190 estimated ~11 bb/100 by applying the costly-band reduction to cells
nobody had re-measured. Measured directly at the shipped configuration,
144 spots weighted by the mix the new sessions produced:

| cell | before (n=67) | after (n=24) |
|---|---|---|
| flop / opening | +0.0145 | +0.0179 |
| flop / facing | +0.3614 | **-0.2945** |
| turn / opening | +0.0148 | +0.0125 |
| turn / facing | +0.0760 | **-0.1355** |
| river / opening | +0.0663 | +0.0242 |
| river / facing | +0.6127 | **-0.1713** |
| **weighted** | **+15.26 bb/100** | **-7.16 bb/100** |
| 95% interval | +4.73 to +25.79 | **-17.51 to +3.19** |

**The negative point estimate is not a finding.** Every negative cell has
a standard error as large as its value and the interval spans zero. A
strategy cannot systematically beat a fuller solve of itself; the two are
now close enough that this method cannot separate them. The claim is
**"the cost went from clearly present to too small to detect"**, nothing
stronger.

Supporting shape: worst single decision **73.25 -> 4.80 bb**, decisions
over 1 bb down to **7%**, median decision **0.008 bb**.

**A limitation this exposed and worth recording**: EV loss against a
FIXED reference opponent measures how well a strategy exploits THAT
opponent, not correctness. A row can beat the reference's own row against
the reference's opponent model without being better in equilibrium, which
is why negative values occur at all and why a near-zero result must not
be read as "correct".

### Speed, paired — the mildest of four attempts to measure it

Eight sessions back to back, alternating configurations, **the same hands
dealt to both arms**:

| | old (100/26) | new (140/140) | |
|---|---|---|---|
| median | 0.11s | 0.58s | 5.2x |
| p90 | 1.59s | 1.99s | **1.25x** |
| worst | 2.39s | 2.30s | **0.96x** |
| flop | 1.56s | 1.93s | 1.24x |
| turn | 0.81s | 0.83s | 1.02x |
| river | 0.11s | 0.62s | 5.44x |
| within 5s | 100% | 100% | |
| defects | 0 | 0 | |

**The worst case did not move and p90 rose 25%.** The 5x median is the
river going 0.11 -> 0.62s, and the median happens to sit in that band.

**Three earlier attempts to price this all overstated it** — M190's
validation said flop 3.19s and median 1.03s; paired it is 1.93s and
0.58s. Each compared runs made hours apart on a machine that drifts up to
1.7x. One of those numbers nearly caused this change to be reverted.

### Benchmark 2 on its own

5,255 decisions over 2,400 hands: median 0.59s, p90 2.09s, worst 3.82s,
100% inside five seconds, **zero defects and zero uniform rows in all
twenty sessions**. Across both benchmarks: **10,607 decisions, zero
defects.**

### What is NOT established

144 spots with a twenty-big-blind interval says the cost fell a long way,
not where it landed. And the reference shares every model limitation with
the shipped solve — the bet-size menu, street isolation, terminal pricing
— so this bounds one source of error and says nothing about the others.

## M193 — the estimate tightened, and a config knob found dead

Benchmark recommendation 2, plus a finding it turned up on the way.

### Rec 2: the cost, at 399 facing spots

M192 measured **-7.16 bb/100** from 144 spots and warned the negative
point estimate was noise. It was. Sampling only the cells that carry the
uncertainty:

| config | n facing | bb/100 | 95% interval | half-width |
|---|---|---|---|---|
| old (100/26) | 201 | +15.26 | +4.73 to +25.79 | 10.53 |
| shipped, 24/cell | 72 | -7.16 | -17.51 to +3.19 | 10.35 |
| **shipped, 133/cell** | **399** | **+3.44** | **-1.02 to +7.91** | **4.47** |

All three facing cells moved back across zero as the sample grew
(flop/facing -0.2945 -> +0.0667). **The cost fell 15.3 -> 3.4 bb/100, a
77% reduction**, and what remains is still not separable from zero — but
the interval is now less than half as wide, and 15.26 sits far outside
it.

Distribution at the shipped config: median facing decision **0.0251 bb**,
13.0% over 1 bb, 1.3% over 5 bb, worst **10.34 bb** (was 73.25).

**Rec 2 also reordered rec 1.** Rec 1 was written believing the cost was
~11 bb/100; with the level unmeasurable, testing knobs could not be
adjudicated. Tightening first was the prerequisite, and it establishes
there is up to ~8 bb/100 left by the upper bound — enough to be worth
chasing.

### F48: `PATH_QUERY_EQUITY_SAMPLES` is inert on the production flop path

Rec 1's first target was equity samples — shipped 30 against a reference
believed to be 200, the largest apparent configuration gap. All three
arms returned **byte-identical** values.

`parallel_board_equity_table` is what production injects, and since M176
it calls `build_shared_runout_equity_table(samples=
SHARED_RUNOUT_FLOP_SAMPLES)`, deliberately not forwarding the caller's
count because 30 is a PER-PAIR number and shared runouts need far more.
That choice is correct and was documented in `api/parallel.py`. The
consequence was not: **the constant still reads as though it controls
flop equity precision, and has controlled nothing there for eight
milestones.** Verified directly — samples=30 and samples=200 on the same
board and seed give max difference **0.0**.

**Two claims this invalidates.** M131 described the postflop budget as
three settings that "move together"; samples has not moved anything on
this path since M176. And every study quoting a reference as "200
samples" was really running 320 shared runouts — **the same table the
shipped solve gets**. Reference and shipped have identical equity; they
differ only in classes (169 vs 140) and iterations (1500 vs 250). That is
a large part of why M192's measured gap is so small.

Pinned by `test_the_per_pair_sample_count_is_inert_on_the_production_
flop_path`, which fails loudly if `samples` ever becomes live again so
the comment stops being true noisily rather than quietly.

### What rec 1 reduces to

With samples inert and iterations already measured useless (M190), the
only remaining measurable gap between shipped and reference is **the 29
classes between cap 140 and the full 169**. Everything else the two share.

## M194 — rec 1 completed: configuration is exhausted

Benchmark recommendation 1 was to re-test the settings rejected on
frequency error, since M190 had reversed one of them for the largest
accuracy gain in the product's history. Completed, and the answer is that
there is nothing left in configuration.

### Every knob, now scored on money

| knob | shipped | tested | result | separable |
|---|---|---|---|---|
| range cap | 26/100 | 140 | **-0.8250 +/- 0.1871, 4.41 sigma** | **YES (M190)** |
| iterations | 250 | 1000 | +0.0911 +/- 0.0632, 1.44 sigma | no (M190) |
| equity samples | 30 | 100 / 200 | **inert — byte-identical tables** | n/a (M193) |
| range cap | 140 | 169 (all) | -0.1787 +/- 0.1136, 1.57 sigma | **no** |

The cap-169 arm is better on 68 spots and worse on 59, a 16% mean
reduction that does not clear two standard errors. By street it is flat
on the flop (0.8138 -> 0.8009), slightly worse on the turn (0.7919 ->
0.8144), and better only on the river (1.7101 -> 1.1644). Not adopted —
and since it would cost latency for no measurable gain, declining is also
the cheap choice.

### What that means

The shipped solve and the reference now share their equity tables
entirely (F48), so the only differences left are iterations and 29
classes — **and neither is worth anything measurable.** The residual
**+3.44 bb/100 (95% -1.02 to +7.91)** is therefore not addressable by any
setting this engine exposes.

Two possibilities remain, and this measurement cannot distinguish them:

1. **The residual is genuinely near zero** — the interval includes it.
2. **It is structural**, in the model that the reference SHARES and
   therefore cannot see: a handful of bet sizes rather than a continuum,
   each street solved in isolation (measured at ~10pp of behaviour on its
   own, M99), the dead-pot terminal convention (F45).

**Either way, tuning is over.** Further gains need a better REFERENCE —
one that models what both currently miss — not a better setting. That is
a materially larger piece of work than anything in this sequence, and it
is the honest next step rather than another sweep.

### The sequence, end to end

The benchmark's five recommendations are complete: rec 1 exhausted
(here), rec 2 tightened the estimate to +/-4.47 (M193), rec 3 was a
method change already applied throughout (paired latency measurement),
and recs 4 and 5 were deliberate non-actions — record what the reference
cannot see, and leave speed alone.

Cost over the sequence: **15.26 -> 3.44 bb/100**, a 77% reduction, for a
paired latency price of p90 1.25x and a worst case that did not move.

## M195 — scoping a better reference, and finding the structural gap is huge

M194 concluded that configuration is exhausted and that further gains
need a better REFERENCE — one modelling what the shipped solve and its
reference both miss. This scopes that, and the scoping produced the
largest measured defect in the product.

### A full chained reference is unaffordable, measured not assumed

`solve_flop_turn` and `solve_flop_to_river` already exist, so this is a
cost measurement rather than a build. One board, increasing range width,
hard budget per arm:

| cap | combos | flop only | + turn | + turn + river |
|---|---|---|---|---|
| 10 | 52 | 0.1s | 8.8s | 206.6s |
| 20 | 120 | 0.3s | 77.4s | 939.1s |
| 40 | 269 | 0.3s | 187.3s | over budget |
| 80 | 564 | 0.7s | **862.0s** | over budget |

**O(N^2) confirmed** (269 -> 564 combos: 187 -> 862s, exponent 2.06).
Extrapolated to reference-grade width (~1,100 combos): **~55 min per spot
for flop+turn alone**, so a 400-spot study is **366 hours**. The three-
street chain is two further orders of magnitude beyond that.

Production cannot chain either: at the shipped cap 140 (~950 combos) a
chained flop solve is ~41 min against today's 1.93s.

### But the gap it would reveal is systematic, and enormous

The full reference cannot be built — the BIAS it would expose can be
measured cheaply, at narrow width, across many spots. 30 flop spots from
real play, cap 20, both arms on the SAME range so only depth varies:

| | |
|---|---|
| aggression shift when the turn is modelled | **+0.2291 +/- 0.0249** |
| significance | **9.21 sigma** |
| spots more aggressive when chained | **30 of 30**, none fewer |
| mean \|delta\|, median, worst | 0.2291, 0.2290, 0.4522 |

**Solving one street at a time makes the advice systematically ~23
percentage points less aggressive.** Thirty spots, no exceptions, at nine
standard errors. Nothing else measured in this project comes close to
that consistency.

### It contradicts M99, and the reconciliation matters

M99 measured this gap once and found the OPPOSITE sign: all-in share
0.5652 (flop only) -> 0.5099 (+turn), i.e. isolation made it MORE
aggressive. That spot ran at **SPR ~1.5**; these run at **SPR ~16**.

The plausible mechanism, stated as a hypothesis and not a finding: with a
deep stack, seeing a future street gives room to realise equity and
semi-bluff, so modelling it raises aggression; at low SPR there is no
room, and the isolated solve over-commits instead. **One spot was never
enough to establish a direction**, which is the fourth time this project
has learned that.

### What this does to everything measured in this session

Every EV figure in M182-M194 was scored against a flop-only reference.
That reference shares this bias with the shipped solve, so it cancels
from the comparison — which is exactly why those numbers were always
described as a **lower bound**. This quantifies the bound: the shared
model error is worth **~0.23 of aggression** on the flop, systematically,
where the residual convergence error is now **+3.44 bb/100 and not
separable from zero**.

**The structural gap is the whole remaining error, and it is much larger
than the one that has been optimised.**

### What can and cannot be done about it

**Cannot**: chain in production (41 min/request at shipped width), or
build a chained reference to measure against (366 hours).

**Can**, in rough order of value:

1. **Establish the SPR dependence.** The sign flipped between SPR 1.5 and
   16 across two studies. If the bias reverses at low SPR, a single
   correction is impossible and the shape of the fix depends entirely on
   this. Cheap: the same 30-spot measurement at two or three stack
   depths.
2. **Disclose it.** The product currently tells players nothing about
   this, and it is larger than every disclosed caveat combined.
3. **Consider a correction** only after (1). The per-spot spread is wide
   (0.09 to 0.45), so a flat offset would be crude, and applying one
   without knowing the SPR dependence would be worse than the bias.

**Do not** attempt a chained production solve or a chained reference at
current speeds; both are measured unaffordable by two orders of
magnitude.

## M196 — the street-isolation bias flips sign with stack depth, and 80% of play is on one side of it

M195 measured street isolation as systematic and large (+0.2291, 30 of
30, 9.21 sigma) and noted it contradicted M99, which found the opposite
sign on one spot. The single structural difference was stack depth —
M99 at SPR ~1.5, M195 at SPR ~16. M195 named settling this as the top
recommendation, because it decides whether any correction is possible.

### The sign does flip, monotonically

Same paired design, same 12 spots, pot fixed at 6bb, varying only
`effective_stack_bb`:

| stack | SPR | delta | sem | sigma | agree | action menu |
|---|---|---|---|---|---|---|
| 9 | 1.5 | -0.1564 | 0.1098 | 1.42 | 7/5 | **2** (no sized bet) |
| 20 | 3.3 | -0.0099 | 0.1185 | 0.08 | 9/3 | 3 |
| 45 | 7.5 | **+0.0981** | 0.0268 | 3.66 | 11/1 | 3 |
| 97 | 16.2 | **+0.2183** | 0.0398 | 5.48 | **12/0** | 3 |

Monotone in SPR, and the deep arms are near-unanimous (23 of 24) where
the shallow ones are a coin flip. **M99 and M195 are both right, at their
own depths.**

### The obvious confound was checked before the finding was believed

At pot 6.0 a 2.5x-pot raise is 15bb, so a 9bb stack cannot offer a sized
bet at all and its only aggressive action is all-in. A sign flip that
coincided with the menu collapsing would be a statement about the TREE,
not about depth.

**It does not coincide.** The flip happens between SPR 3.3 and 7.5, where
both arms offer the identical 3-action menu; only the 9bb row loses its
sized raise, and the sign has already changed by then. The menu was
recorded per depth for exactly this reason.

### Which regime real play is in — measured, not assumed

The practical size of the bias depends entirely on where real decisions
fall. Derived from 8,032 recorded opening decisions (pot recovered from
the tree's own 2.5x-pot size, stack from `max_affordable_bb`):

| street | n | median SPR | p25 | p75 | SPR < 5 | SPR >= 5 |
|---|---|---|---|---|---|---|
| flop | 4,217 | 9.5 | 8.6 | 19.5 | 21% | **79%** |
| turn | 3,815 | 9.5 | 9.5 | 19.5 | 19% | **81%** |
| all | 8,032 | 9.5 | 9.5 | 19.5 | 20% | **80%** |

So the consistent-positive regime is the normal one: for four decisions
in five, the shipped flop advice is systematically ~10-22pp LESS
aggressive than a solve that also plays out the turn.

### Disclosed, not corrected — and a flat correction is now impossible

M195 established the reference cannot be built (366 hours) and production
cannot chain (41 min/request). This adds a second, independent reason not
to correct: **the sign reverses**, so a flat offset would be wrong at one
end of the range, and applying it below the threshold would point a
short-stacked player the opposite way.

`STREET_ISOLATION_NOTE` therefore ships as a caveat, gated on **both**
conditions and neither is cosmetic:

* `STREET_ISOLATION_STREETS = ("flop",)` — the only street whose
  isolation gap has been measured. The turn's never has; the river has no
  later street to model. **M168 is the standing example** of what
  assuming a flop measurement transfers to another street costs.
* `STREET_ISOLATION_SPR_MIN = 5.0` — below it the measured direction is
  the opposite one, so the note would be actively wrong rather than
  merely unsupported.

The copy states two limits explicitly: this is the gap to a fuller model
**of the same kind, not the distance to correct play** (the chained solve
is more complete, not right), and the direction reverses at short stacks.

Five mutations killed: removing either gate, weakening `>=` to `>`,
flipping the direction word, and drifting a constant from the copy.

### What it does to this session's accuracy numbers

Every EV figure in M182-M194 was scored against a flop-only reference
that shares this bias, so it cancels from the comparison — which is why
they were always reported as a lower bound. This quantifies the bound:
the shared model error is worth **0.1-0.22 of aggression** where the
residual convergence error is **+3.44 bb/100 and not separable from
zero**. **The remaining error is structural, not configurational**, and
M194's conclusion that configuration is exhausted is reinforced rather
than superseded.
