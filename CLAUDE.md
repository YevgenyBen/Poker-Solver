# Poker Solver

A Texas Hold'em GTO solver engine, coupled with a webview so a user can explore
its output interactively.

## v1 scope

- Engine: Python (NumPy for hot paths).
- Preflop only — RFI / 3-bet / 4-bet / jam ranges for a chosen effective stack
  depth. No board cards, no postflop streets (yet).
- Heads-up (2 players) only, not multiway.
- CFR+ solver over a 169-starting-hand-class abstraction (single bet size per
  street, 4-raise cap before forced jam-or-fold).
- Backend: FastAPI (`GET /solve/{stack_bb}`), on-demand solve with an
  in-process cache + startup pre-warm of common stack depths.
- Frontend: React + TypeScript (Vite) in `frontend/`. `npm run dev` for a
  hot-reloading dev server (proxies `/solve` to FastAPI on :8000);
  `npm run build` produces `frontend/dist/`, which `api/main.py` serves
  directly at `/` when present. See `frontend/README.md`.

Full postflop support is out of scope for v1 but the module boundaries (e.g.
an injected `payoff_fn` at terminal tree nodes) are meant to allow adding it
later without a rewrite.

## v2 progress (in progress)

Growing toward a full-table, any-street advisor — see the roadmap plan for
the phased approach. Landed so far:

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

### Phase C — Postflop (in progress)

Design pass completed (see the roadmap plan) with two confirmed direction
calls: postflop moves to concrete two-card **combos**, not the 169-class
abstraction (blocker effects are a first-order postflop concern, unlike
preflop); and the first milestone targets a **flop-only** tree (runouts
averaged at the terminal) before building the chance-node machinery multi-
street chaining needs.

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

## v3 vision (future) — live-table advisor

Discussed with the user while scoping M16, recorded here rather than
left implicit: the longer-term goal beyond v2's demo/range-chart
tooling is a live-table advisor — a user mid-hand describes what
actually happened (any action sequence, any street, eventually
multiway) and gets advice grounded in a real solve for that exact
situation, not a curated demo range.

Two gaps identified when scoping this, pulling in different directions:
**flexible situation input** (`solve_flop*` only ever consumed curated
hardcoded ranges or, as of M15, one fixed preflop line — M16 is the
first general step past that, M23 the second, M24 the third, M25 the
fourth and last) and **real-time speed** (measured solve times, M12-M14,
run ~20s to several minutes even for small ranges). The user chose
flexible input first, since speed work is easier to scope once the
shape of query it needs to serve fast is known. Both gaps are now
closed end to end: the 4-phase real-time-speed roadmap (M17-M21), wired
into a live endpoint by M22, connected to a real, user-derived situation
by M23, exposed as a real live endpoint accepting an untrusted
action-path description by M24 (`POST /solve_flop_from_path` — a hit
costs ~0.15-0.2ms, a real derived-situation miss ~17-21s, capped per
M24's own Finding 1 from what would otherwise be hours), and finally
given a real interactive frontend by M25 (`POST /preflop_walk` plus a
rebuilt `ActionPathSolver.tsx`, replacing M24's own curated 3-preset
selector with a general step-by-step wizard over the exact same, fully
general backend M24 already shipped).

**M26 update — turn-level advice ships too, extending both threads
above one street further:** `POST /solve_turn_from_path` reaches a real
turn decision (not just a flop-level number improved by real turn
action baked in), and confirms the real-time-speed thread's own
`solve_flop_turn`/`solve_flop_to_river` (M12/M13) had already computed
real turn/river strategies all along — reading one out live cost
nothing new (~0.04ms, after a solve that was already being paid for).
A real, caught-before-shipping finding along the way: the derived-range
cap that works for the flop (`MAX_PATH_QUERY_CLASSES_PER_SIDE=6`) does
*not* carry over to the turn — `solve_flop_turn`'s steeper cost curve
turned the same cap into a 454s real request; a separately-measured,
smaller cap (`MAX_TURN_PATH_QUERY_CLASSES_PER_SIDE=2`) brought it back
to ~46s, in the same bracket `/solve_flop_to_river` was already
accepted in. What remains, deliberately: river-level advice one street
further (already de-risked cost-wise by this milestone's own
measurements — a two-hop river walk measured ~0.002ms), an interactive
flop-action wizard (this milestone's own flop-line input is a curated
preset dropdown, mirroring `ActionPathSolver.tsx`'s own M24-before-M25
history), and multiway postflop solving — the only thing across this
entire multi-milestone thread that has never been scoped at all.

**M29 update — the specific, common exception to that last line ships:**
true 3+-live-player postflop solving remains unscoped, but a real
multiway-*origin* hand that folds down to two live players now gets
real flop/turn advice through both live endpoints and both wizard
frontends — `poker_solver/game_tree.py`'s new `postflop_action_order`
(a real poker rule, not a heads-up-only guess) correctly maps whichever
two positions actually survive, at any origin table size, closing the
last of the "three duplicated position-unpack" sites the diagnostic's
§4 named. A related, previously-unknown gap surfaced and fixed along
the way: `derive_ranges_from_path`'s own reach-multiplication had no
confidence signal, and a real deep 6-max line was measured producing an
exactly-uniform, fabricated-looking derived range — `PathScenario`
gained its own `trained` field for this reason, mirroring M28's signal
one layer earlier in the pipeline (not yet threaded through to either
endpoint's own response — a named, deliberate gap, not a silent one).

### The real-time-speed roadmap

Picked up after M16: real-time speed splits into genuinely different
levers, and the first one tried didn't pan out. A "batch board-equity
computation across matchups" attempt (chunking `build_board_equity_
table`'s per-pair `hand_eval.best_hand_rank_batch` calls into fewer,
larger ones — exactly what that module's own M10-era comment had
flagged as "the natural next optimization") was implemented and
measured before being trusted: at the same 23/~85-combo checkpoints
M10 used, it delivered only ~0-13% speedup, not the assumed win.
Profiling why: `best_hand_rank_batch`'s own vectorized computation
already accounted for ~81% of total time even in the *original*
per-pair implementation, and that cost scales with total data volume
(N² combo pairs × runout samples), not with how many separate Python
calls it's split across — so consolidating calls removed overhead that
was never the dominant cost. Discarded (never committed) once measured
— a real, cheap-to-discover dead end, not a hidden failure.

Given that ceiling, the deepest available path is a 4-phase program,
each phase depending on the one before it:

1. **Card abstraction (M17)** — bucket strategically-similar combos
   together, shrinking N directly. Attacks the O(N²)/O(N) cost at its
   root, the thing the failed batching attempt structurally couldn't do
   (it only ever reduced the constant factor around a fixed N).
2. **Canonicalization** — recognizing when two situations (board,
   action-history shape, stack depth) are strategically the same, so a
   library lookup can hit instead of every situation being unique.
3. **Offline precomputed spot library** — batch-solve a broad set of
   canonical situations ahead of time, no live time pressure, stored
   indexed by phase 2's canonicalization.
4. **Live query path** — a real situation (via M16's `derive_ranges_
   from_path` for the action history, hero's real hand, the actual
   board) gets canonicalized and looked up; a hit is instant, a miss
   falls back to an on-demand solve.

Card abstraction has to come first: precomputing exact-combo spots
doesn't achieve enough compactness to build a real library against.

**M18 update — phase 1's own live-solve speedup didn't materialize,
measured, not assumed:** wiring card abstraction into a real
`solve_flop`-shaped CFR solve (M18) found no meaningful speedup at 23-
or ~85-combo scale (0.95x-1.11x — break-even to slightly slower) and
measurably worse strategy accuracy than its own equity-level error
predicted. The reason traces cleanly to M17's own finding:
equity-table construction, not the CFR tensor step, dominates total
cost at these scales, and bucketing can only ever *add* a bucket-table
build on top of the full N×N equity table already required to derive
the bucketing signal — so a miss on the phase-3 library still costs
roughly what it costs today, not less. This changes point 4 above:
card abstraction doesn't make an on-demand miss cheaper by itself.
Phases 2-3 (canonicalization + an offline precomputed library) remain
the real lever for live speed, since they sidestep live equity-table
construction entirely on a hit — that's where M18's own finding says
the actual cost lives. Card abstraction may still matter for *offline*
library-building cost (batch-solving many canonical situations ahead
of time, where CFR iteration count/tensor size matters more relative
to a one-time equity build per situation) — not measured yet, a real
open question for whichever milestone scopes phase 3.

**M19 update — phase 2 (canonicalization) ships as a standalone
primitive, same pattern M17 set for phase 1:**
`poker_solver/canonicalize.py` provides exact, lossless suit-relabeling
canonicalization for boards and hole cards, plus bucketed stack-depth
rounding — the piece a future phase-3 library will actually index by.
Not wired into anything live yet. One real correctness finding along
the way: a naively-simple single-pass canonicalization algorithm was
measured against the true suit-isomorphism minimum before being
trusted, and found to under-collapse paired-rank boards (1,911 distinct
flop forms instead of the true 1,755) — fixed by searching the full
24-permutation suit-automorphism group instead, which also turned out
simpler than the naive design it replaced. Confirmed by exhaustive
enumeration: 22,100 flops collapse to 1,755 canonical forms, 270,725
turns collapse to 16,432, and 2,598,960 rivers collapse to 134,459 —
real numbers a future phase-3 library-sizing decision can use directly.

**M20 update — phase 3 (an offline precomputed spot library) ships as a
standalone primitive with its key contract proven, not just assumed:**
`poker_solver/library.py` batch-solves real boards, dedupes by M19's
canonical (board, bucketed-stack) key, and stores each distinct
canonical solve. The real risk this phase could have gotten wrong
silently — whether a canonical hit actually serves *any* isomorphic
real board, or only the literal board a solve happened to run against —
was resolved by constraining `build_library` to class-frequency ranges
only (never raw asymmetric combo dicts, which don't have the suit-
blindness property the whole scheme depends on) and confirmed end to
end: a library built by solving one real board is queried with a
*different*, merely suit-isomorphic real board never solved directly,
and the returned strategy matches a fresh direct solve exactly. Phase 4
(a live query path with canonicalize-then-lookup-then-fallback-to-
on-demand-solve, plus API/frontend wiring) is the roadmap's final,
still-unscoped phase — now unblocked, since phases 2 and 3's contracts
are both proven, not just built.

**Correction, from M21:** that exact-match claim above held for the
specific board pair M20's own test used, but only because that pair's
second board was, unnoticed, literally the first board's own canonical
form (an identity suit-map). Tested against a genuinely different real
board instead, the match is not bit-exact — flop-level equity is Monte
Carlo sampled, and the deck's suit-dependent iteration order means the
same seed draws different specific runouts for two differently-suited
isomorphic boards. The actual crux property (a hit correctly *serves*
any isomorphic board without re-solving) still holds; see M21's own
entry above for the precise, corrected statement and the fix applied
to the test that first surfaced this.

**M21 update — Phase 4 (a live query path) ships, closing the
roadmap's engine-level work:** `poker_solver/library.py`'s `query_
strategy` completes the loop this roadmap set out to build in M17:
canonicalize a real query, look it up, return instantly on a hit, fall
back to an on-demand solve on a miss (via `build_library`'s own logic,
not duplicated), and cache the result in place so the next hit on that
canonical spot — or any real board merely isomorphic to it — really is
instant. All four phases are now done: card abstraction (M17) was
tried and found not to be the lever (M18: equity-table construction,
not the CFR tensor step, dominates cost, so shrinking hand count
doesn't shrink the real bottleneck); canonicalization (M19) and an
offline precomputed library (M20) sidestep that bottleneck entirely on
a hit instead of trying to speed it up; this phase wires hit/miss into
one live entry point and measures the real payoff: a hit costs
**~0.15ms**, a miss costs **~0.95s** (in the same ballpark as M20's own
~0.92s/board figure, since a miss *is* a one-board `build_library`
call), a **~6,313x** ratio — the concrete, measured answer to the
question this roadmap exists to answer, not an assumed one.

What's deliberately still not done, now that the roadmap's own
engine-level work is complete: no `api/main.py`/frontend wiring (a live
endpoint calling `query_strategy` against a real, persistent, shared
library, including a concurrent-miss serialization decision this
milestone didn't need to make), and no connection to M16's `derive_
ranges_from_path` (translating a real, user-described action history
into `query_strategy`'s `hero_classes`/`villain_classes` inputs — a
mostly direct fit, since `derive_ranges_from_path` already returns
`StartingHand`-keyed ranges for a preflop `StrategyResult`, but with
one real wrinkle worth naming precisely: `PathScenario.stacks` is a
per-position dict, not the single `effective_stack_bb` float `query_
strategy`/`solve_flop` expect, so an arbitrary path needs an explicit
"both live positions' remaining stacks are equal here" check before
that hookup is safe). Both are natural next milestones — the same
two-engine-primitives-then-one-wiring-milestone pattern M12/M13-
before-M14 already established.

**M22 update — the first of those two follow-ons ships:** `GET /solve_
flop_cached` calls `query_strategy` live, against a fixed demo range
(not yet a real user-described one — that's M23's job). Measured
through the real endpoint: a hit costs **~0.20ms**, a miss costs
**~1.55s**, a **~7,763x** ratio. The connection to `derive_ranges_from_
path` remains open.

**M23 update — the second follow-on ships too, closing both:**
`poker_solver/library.py`'s `query_strategy_from_path` bridges a real
preflop `StrategyResult` + a real walked `action_path` into `query_
strategy`, completing the loop M21's own write-up predicted. The
"stacks equal" check M21 anticipated turned out to be the wrong check
— the correct one is `isinstance(path_scenario.node, TerminalNode)`,
proved sufficient (not just necessary) from `game_tree.py`'s
no-side-pots construction; see M23's own Phase C entry for the full
argument. Still not done: a live endpoint accepting a real, untrusted
action-path description (deliberately deferred, same reasoning as
every other bridge milestone in this roadmap) and multiway postflop
solving (out of scope for this entire roadmap, not just this
milestone).

**M24 update — the live endpoint ships too, closing this roadmap's
product-surface work:** `POST /solve_flop_from_path` finally exposes
the full canonicalize -> library -> path-derived-range chain to a real,
untrusted client — the thing M21 first named as remaining, that every
milestone since (M22, M23) closed one piece of. Getting there safely
required two real findings, not just wiring the pieces together: an
uncapped derived range would have cost hours per request (Finding 1,
fixed with a request-time top-K cap, engine layer untouched), and
sharing one canonical-key library across different real situations
would have silently corrupted answers (Finding 2, fixed with a
partitioned per-`(action_path, stack_bb, iterations)` library). Real
measured numbers: a capped miss costs ~17-21s, a hit ~0.1-0.7ms.
Multiway postflop solving remains the only thing this whole roadmap +
its flexible-input companion thread never scoped — explicitly future
work, not this project's v2.

## Engine is standalone

`poker_solver/` has zero dependency on the API or any web framework — it's a
plain library usable on its own (`import poker_solver; poker_solver.solve_preflop(...)`).
This is enforced, not just true by convention: `tests/test_package_boundary.py`
scans every file under `poker_solver/` and fails the build if it ever imports
`fastapi`, `starlette`, `uvicorn`, or `api`. `api/` depends on `poker_solver`,
never the reverse.

Dependencies are split accordingly:
- `requirements.txt` — the engine only (`numpy`). `pip install -r requirements.txt`
  is enough to use `poker_solver` standalone.
- `requirements-api.txt` — adds the FastAPI backend (`-r requirements.txt` + `fastapi` + `uvicorn`).
- `requirements-dev.txt` — everything needed to run the full test suite (`-r requirements-api.txt` + `pytest` + `httpx2`).

## Workflow rules

- **Always work on a branch.** Never commit directly to `main`. Create a
  feature branch for every change, however small, and only merge into `main`
  when the user explicitly says to merge.
- **Tests are mandatory.** Every function gets a test.
  - Python: follow the `tests/` + pytest convention (one test module per
    source module, e.g. `poker_solver/foo.py` -> `tests/test_foo.py`).
  - Frontend: Vitest + React Testing Library, colocated as `*.test.ts(x)`
    next to the file it tests (e.g. `frontend/src/hands.ts` ->
    `frontend/src/hands.test.ts`).
- **Re-run the full suite after every change** — `python -m pytest tests/ -v`
  and, for anything under `frontend/`, `npm test` there too — before
  considering any change done, not just the tests for the file just touched.
- Ship one coherent improvement per PR (matches how this project started:
  scaffold -> missing-test PR -> merge).
