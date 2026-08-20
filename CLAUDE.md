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
first general step past that) and **real-time speed** (measured solve
times, M12-M14, run ~20s to several minutes even for small ranges).
The user chose flexible input first, since speed work is easier to
scope once the shape of query it needs to serve fast is known. Now
addressed at the engine level by the 4-phase real-time-speed roadmap
below (M17-M21): canonicalized situations are looked up in a
precomputed/cached spot library, a hit costing ~0.15ms and a miss
falling back to an on-demand solve (~0.95s) — not yet wired into a
live product surface.

Where an LLM fits, and deliberately doesn't: translating a player's
natural-language hand description into the structured input the engine
needs, and narrating the engine's own computed frequencies back in
plain language — an input/output layer around the solver, never a
replacement for it. Feeding an LLM general poker strategy content and
letting it generate advice directly, bypassing the solver, was
considered and rejected: it would trade a mathematically grounded GTO
computation for LLM pattern-matching that can sound just as confident
when wrong — exactly what a real solver exists to avoid.

Not scoped yet, deliberately: multiway postflop solving, the LLM layer
itself, and product-surface wiring for the precomputed-spot caching
system that now exists at the engine level (`poker_solver/library.py`,
M19-M21 — see "The real-time-speed roadmap" below). M16 (arbitrary
action-path range derivation) is the first concrete bridge piece —
everything else here depends on a general way to describe "the current
situation" first.

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
