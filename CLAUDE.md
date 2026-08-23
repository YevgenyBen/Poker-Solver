# Poker Solver

## Current state (read this first)

A Texas Hold'em GTO solver engine plus a web UI for exploring it. The
engine is the product; the frontend is a tool for driving it.

### What it does today

Given **your hole cards, the board, your position, and the action so
far**, it returns GTO advice for the decision you actually face — at
every street (preflop through river) and every supported table size
(heads-up, 3/6/9-max). One endpoint does this: **`POST /advise`**.

### Module map

    poker_solver/          the engine — no FastAPI dependency, enforced by
                           tests/test_package_boundary.py
      game_tree.py         betting trees (GameConfig preflop, StreetConfig postflop)
      cfr.py               solve() = exact CFR+ (2 players), mccfr_solve() = sampled (N players)
      equity.py            preflop equity + MultiwayEquityCache
      board_equity.py      board-aware pairwise combo equity
      multiway_board_equity.py   board-aware N-way combo equity
      hand_eval.py         hand ranking (prime-product lookup table, M48)
      chance.py            chance nodes — build_chance_node (exact) / build_mccfr_chance_branch (sampled)
      solver.py            the public solve_* API + derive_ranges_from_path
      library.py           canonical spot library (canonicalize -> lookup -> solve on miss)
      canonicalize.py      suit-isomorphism canonicalization
      combos.py, cards.py, starting_hands.py, abstraction.py, strategy_format.py

    api/                   one-way layering, no cycles: config <- caches <- solving <- main
      config.py            every tunable constant, each with its measured justification
      caches.py            _SolveCache + one instance per endpoint (self-registering)
      solving.py           all _get_or_solve_* / _query_* / _advise* orchestration
      main.py              routes, validation, response shaping, app wiring
      schemas.py           Pydantic request/response models

    frontend/src/          React + TypeScript (Vite)
      components/          AdviseSolver is the front door; the rest are narrower demo tools

### Where to change what

| To change… | Go to |
|---|---|
| a cost cap or iteration budget | `api/config.py` (each constant carries its measurement) |
| how a request is answered | `api/solving.py` |
| a URL, status code, or response shape | `api/main.py` + `api/schemas.py` |
| solving itself | `poker_solver/` |

### Known constraints — read before "improving" these

- **Both flop decisions now share one tree (F12 fixed in M88)** — the
  mid-flop cell runs `solve_flop` at the canonical library's own config
  instead of `solve_flop_turn`'s narrower one. Don't "optimize" it back
  onto the turn cache: that sharing is what made the raise sizes differ
  between two decisions on the same street.
- **The library solves at a BUCKETED stack depth, and the bucket rounds
  DOWN (F13, fixed M95).** 5bb buckets are what make canonical reuse
  work. They used to round to *nearest*, which put the solved depth above
  the real one and made the advice name bets the player could not make —
  a 100bb limped pot leaves 99bb and came back `all_in:100.00`.
  `canonical_stack_depth` now floors, so `canonical <= real` holds and
  every size the tree derives is affordable by construction. The price is
  a full bucket of depth error instead of half; measured across every
  node of a real solve, that costs under 1% of probability mass at its
  worst and nothing at all at three of four depths. **Do not "improve"
  this back to round-to-nearest.** Sub-bucket stacks are used unbucketed
  — clamping them up to one bucket was tried and reintroduced the bug.

- **The sampled solver does NOT use CFR+'s regret clamp, and that is
  deliberate (M71).** `mccfr_solve(floor_regret=False)` is the default.
  Clamping regret at zero is a win in the exact solver (`_solve_recurse`
  still does it) but a ratchet under sampling: it discards negative
  regret while accumulating positive, so the noisiest action — the
  all-in, which swings a whole stack — collects spurious regret that
  more iterations only compound. Measured at 6-max over 3 seeds: AA's
  jam 0.199 -> 0.032 (heads-up reference ~0.031), T7s's UTG fold
  0.744 -> 0.938. **Exception: 9-max keeps the clamp** (`api/config.py`)
  — plain CFR converges more slowly and 9-max's budget gives each seat
  only 333 traversals, where it goes the wrong way. Published DCFR was
  tried and was worse than plain CFR.
- **Both solvers weight the time-average LINEARLY (M69 sampled, M71
  exact).** `current_strategy()` returns an exactly uniform
  1/num_actions before regrets accumulate, so equal weighting leaves a
  long run's average contaminated by its own warm-up. The exact solver
  had this too and it was not harmless — a toy AA-vs-72o game averaged
  0.656 at 500 iterations against a ~0.97 equilibrium. **Any "trusted
  heads-up reference" number predating M71 is suspect for this reason.**
- **Multiway preflop SIZING is structurally wrong, and no budget fixes
  it (M98).** The split among non-fold actions was long filed as "not
  converged at this budget". It is not a budget problem: at 12,000
  iterations and 400 equity samples — the most converged, least noisy
  setting measured — AA jams 0.649 and KK 0.709. More iterations and
  more samples converge ONTO the jam. Cause: every showdown terminal is
  priced `equity * pot - invested` (`cfr._mccfr_terminal_value`), so an
  all-in is priced correctly while every smaller bet is scored as if the
  hand ended immediately — discarding the postflop game that is most of
  a raise's value. The error grows with opponent count, since more
  opponents means more chance the correctly-priced all-in gets called.
  **Why heads-up looks fine is NOT established** — M98 asserted a
  cancellation argument it never measured, and the arithmetic behind it
  does not survive contact with how the solver actually works (a jam's
  value depends on villain's calling frequency against the whole shoving
  range, not on AA alone). Measured: the pricing rule, and that more
  samples/iterations converge onto jamming at 6-max. Inferred, still
  open: why N=2 is unaffected. **Don't try to fix
  this with iterations, samples, or the policy rule** — it needs
  postflop continuation value at preflop terminals. Users are told via
  `sizing_confidence` (M98); the fold-vs-play call is unaffected and
  remains sound at 3/6-max.
- **A crude continuation term does NOT fix the sizing defect — don't
  tune it (M100).** `mccfr_solve(continuation=c, stack_bb=...)` adds
  `c * (equity - 1/n_live) * chips_behind` at terminals with money
  behind, as a cheap stand-in for the postflop game M98 showed the tree
  cannot see. Swept c = 0/0.25/0.5/1.0: AA's jam goes 0.615/0.208/0.417/
  0.374 at 12k and 0.061/0.112/0.287/0.010 at 3k — **non-monotone at both
  budgets**, so it is not capturing the mechanism. `c=1.0 @ 3,000` looks
  like a fix (0.010 +/- 0.005, tightest arm by 10x) and is not: a big
  bonus for keeping chips behind makes the all-in *dominated*, so the
  policy goes purely "never jam" and lands BELOW the ~0.031 reference.
  **The knob can produce any number, so matching the reference does not
  validate it.** A paired 9-seed test (same seed both arms, cancelling
  seed variance) gives c=0 vs c=0.25 a delta of **-0.060 +/- 0.137,
  falling in 5/9** — a coin flip. Kept default 0.0 for reproducibility;
  costs no memory.
  A real fix needs SOLVED flop continuation values, and that milestone
  cannot be justified or costed on this evidence.
- **The pricing flaw reaches the FLOP too, but ~10x smaller (M99).**
  `solve_flop` is flop-only (two unmodelled streets) and serves heads-up
  flop advice. Same board/ranges/pot/stack/sizes, varying only how much
  future betting the tree sees: all-in share **0.5652 (flop only) ->
  0.5099 (+turn) -> 0.4635 (+turn+river)** — ~5pp per street, 10.2pp
  monotone, exact solver so deterministic, not noise. Deliberately NOT surfaced as a caveat:
  5.5pp is an order of magnitude below the preflop distortion, it is one
  spot at SPR 1.5, and flagging every postflop response would devalue the
  preflop warning that marks a genuinely unusable axis. Revisit if
  measured wider and larger.
- **Equity noise explains the sizing INSTABILITY, not its level (M98).**
  A 50-sample multiway equity estimate has error sd 0.091 — **+/-55bb of
  EV in a six-way 100bb pot**, worst measured 141bb — and the cache
  freezes it per key, so CFR optimizes against its own noise rather than
  averaging it out. That is why the jam frequency swings with the seed.
  `equity.py`'s own `MULTIWAY_DEFAULT_SAMPLES = 200` comment warned in
  M8 that 50 samples distorts MCCFR *via the all-in*; `api/config.py`
  overrode it to 50 on fold-rate measurements and the warning was never
  reconciled.
- **9-max preflop output is NOT reliable (M68, measured).** T7s folds
  only 12.5% under the gun at 9 handed, where it should be near 100%
  and 6-max reaches 87.4%. Eight opponents make the sampled variance
  too high for any affordable iteration count. 3-max and 6-max are in
  much better shape. Don't present 9-max advice as authoritative.
- **A precomputed multiway equity table DOESN'T WORK — don't re-try it
  (M68).** It's the intuitive analog of heads-up's disk-cached 169x169
  table and M67 recommended it, but the tuple space can't be collapsed
  without losing hero-opponent interaction (domination, blockers):
  pairwise-derived estimators hit correlation 0.39 at 9-max, and
  bucketing opponents by strength plateaus at ~3x the Monte Carlo noise
  floor regardless of bucket count. Also don't re-try micro-optimizing
  the Python hot path (M67: profiler said `Card.value`/`rank_value`
  dominated, real gain was zero — the M47 trap).
  **What did work:** sharing board runouts across candidates
  (`equity._simulate_equity_shared_board`, M68) — the opponents' hands
  were being re-ranked once per candidate. **6.06x at the equity layer**,
  from M70's interleaved A/B. (M68 itself published 1.95x from a
  cross-session before/after and M70 withdrew it — see "Measuring
  performance" below.)
- **The old "6-max diverges with more iterations" constraint is RETIRED
  (M66 diagnosed, M67 fixed).** It was never a solver bug — the old
  8-class pool was 48.6% premium, so folding AKs under the gun really
  was near-correct and MCCFR converged correctly to a distorted
  question. **Do not** try to fix anything in `_mccfr_recurse` for this;
  M27 proposed exactly that, M66 built it and measured no effect. Still
  pinned by the paired
  `test_six_max_demo_pool_degrades_with_more_iterations` and
  `test_six_max_converges_with_a_realistic_pool`, which now document a
  property of *any* premium-heavy pool rather than a live defect.
- **EVERY decision is reachable now (M84-M89).** `flop_action_path`,
  `turn_action_path` and `river_action_path` each select which decision
  on that street is being asked about; absent means the street's first.
  Works heads-up and multiway. Before this, `/advise` answered only each
  street's opening decision — a player facing a bet could not ask.
- **Multiway turn/river branches are SOLVED on demand (M75) — don't
  remove that.** MCCFR samples one next card per terminal, so the card a
  client actually asks about is almost never one the solve sampled.
  `ensure_mccfr_chance_branch` builds the missing branch and now also
  trains it (`MULTIWAY_BRANCH_TRAIN_ITERATIONS = 100`, ~7-9s). Before
  this, multiway turn and river returned **0 of 132 combos trained,
  every strategy exactly uniform** — always, not occasionally, and since
  the feature shipped. Heads-up is unaffected: its exact solver
  enumerates every card eagerly.
- **Multiway POSTFLOP still answers an easier question than heads-up.**
  M67 fixed the preflop leg (all 169 classes now), but postflop path
  queries cap derived ranges per position
  (`MAX_MULTIWAY_PATH_QUERY_CLASSES_PER_POSITION = 8`,
  `MAX_MULTIWAY_TURN_PATH_QUERY_CLASSES_PER_POSITION = 8`) — measured
  11.5s (flop) / 1.5s (turn). Treat multiway postflop advice as
  correspondingly thinner, and note those caps genuinely bind now, where
  pre-M67 they never did.
- **`trained` / `range_confidence` / `source` exist because output can
  look confident and be fabricated.** Don't strip them for tidiness.
- **`hero_cards` is part of the path-query cache keys — do not remove it
  (M76).** Hero's combo is force-included into the derived range before
  the top-K cap, so the SOLVE depends on hero. When the keys ignored
  hero, the first request for a spot fixed the pool and every later
  request with a different hand got NO advice. Keyed by hand *class*
  (169 values, not 1,326). The suite could not see this because its
  fixture clears caches between tests; the guard is
  `test_advise_gives_every_hero_advice_regardless_of_who_asked_first`,
  which deliberately does not.
- **9-max is marked `solver_confidence: "low"` in `/advise` (M76)** —
  it returns real solves of an under-trained problem (T7s's top action
  UTG is *call*; AA shoves 100bb), and budget cannot fix it. Don't
  present it as GTO.
- **The canonical-library path reports real `trained` flags (M76).** It
  used to return null, documented as structural; it was not — the
  dataclass just didn't carry them. `LibraryEntry.trained` does now.
- **Five `*_from_path` routes are deprecated** (superseded by
  `/advise`), still functional. New callers should use `/advise`.

- **Multiway iteration budgets are per-table-size and MEASURED — do not
  unify them (M72).** Without the CFR+ clamp, AA's jam frequency grows
  with iterations at 6-max (0.033 at 3k -> 0.404 at 12k), while 3-max
  measured the opposite (0.527 at 3k -> 0.120 at 12k). So 6-max ships
  3,000 and 3-max ships 12,000. `test_six_max_jam_frequency_at_the_
  shipped_budget` reads the config and asserts at whatever budget is set,
  so raising it fails loudly.
- **The 6-max jam instability at high budgets is UNEXPLAINED — three
  causes are ruled out (M73).** AA's jam is stable and correct at 3,000
  iterations (~0.03) and swings 0.02-0.52 across seeds at 12,000. It is
  NOT the CFR+ clamp (M71), NOT `current_strategy()`'s uniform fallback
  (M73 — the all-negative row fraction is ~70% in every arm and is
  dominated by never-visited rows, and it *decreases* with iterations),
  and NOT `EXPLORATION_EPSILON` (M73 — 0.002 looked like a clean fix on
  one seed and gave 0.024/0.211/0.516 on three). Don't re-test those.
  **M74 found what it IS:** the policy is bang-bang — `current_strategy()`
  gives AA's jam exactly 0.000 or 1.000 depending on the run. Raise vs
  jam is near-tied under this model, so regret matching oscillates
  wholesale and the average reflects whichever phase a run ended in.
  Linear averaging amplifies this (~0.09) but is not the cause and is
  kept. **M97 built M74's prescribed fix — policy damping, in both the
  forms it named — and measured both WORSE than doing nothing.** At the
  6-max iteration budget (but at 200 equity samples, not the shipped 50 —
  M97 mislabelled this and M98 corrected it; the arms stay comparable
  since all three did the same), three seeds, fresh equity cache: plain
  AA-jam mean 0.056 / spread 0.037, predictive regret matching 0.628 /
  0.604, policy smoothing 0.348 / 0.591, all at the same cost. Prediction
  is a full-information technique and under sampling just amplifies the
  all-in's noise; damping is a lag filter on regret matching's OUTPUT
  while the oscillation is in its INPUT, and enough damping to outlast
  the cycle stops the policy learning (`smoothing=0.99`: AA jams 0.998,
  T7s's fold collapses 0.94 -> 0.34). `optimism`/`smoothing` remain in
  `mccfr_solve`, default 0.0, so the result is reproducible — **don't
  re-try either.** Next attempt should look at the equity model and pool,
  not the policy: damping narrows the 12k seed spread without moving the
  level, which a phase-sampled cycle would not do.
- **Validate solver changes at the SHIPPED operating point.** M71
  measured a real improvement at 3,000 iterations and left the budget at
  12,000, where the property does not hold — shipping a regression to
  `main`. Unit tests could not catch it: the suite's fixtures shrink
  pools and budgets for speed. Run an end-to-end `/advise` check at
  production settings after any solver change.

- **Solve caches are BOUNDED (M93) — `_SolveCache(name, maxsize=N)`
  with LRU eviction.** Nothing evicted anything before, so a
  long-running server accumulated an entry per (spot, stack, hero class,
  action path) forever: heap grew ~0.065 MB per request with no ceiling.
  `_multiway_cache` and `_preflop_raw_cache` stay unbounded on purpose —
  a few dozen entries, 75-140s each, filled by the startup pre-warm.
  **`_SolveCache.lock` is an RLock deliberately**: call sites hold it
  across read-check-write and call `store`/`get` from inside. A plain
  Lock self-deadlocks — it hung the whole suite once.
- **Expensive solves go through `_SolveCache.get_or_compute`, not
  check-then-compute (M92).** The old pattern let N concurrent misses on
  one key each run the full solve — measured at **8 concurrent requests
  doing 8 solves (223s) where 1 was needed (31.7s)**. It was documented
  as an accepted tradeoff because the solves are deterministic, which is
  true about correctness and says nothing about cost. `self.lock` guards
  only the dict; the per-key lock is held across the solve.

### Measuring performance — read before trusting any timing

**This machine drifts.** M70 observed identical workloads running ~1.7x
slower than when M68 measured them (9-max/3k: 418s vs 249s; 6-max/12k:
491s vs 281s). So **absolute wall-clock numbers recorded in different
sessions are not comparable**, and `docs/milestones.md` is full of them.
M68's headline "1.95x" was withdrawn in M70 for exactly this reason.

When making a speed claim, do one of these — never a bare before/after
across sessions:
- **Interleaved A/B in one process** (old and new implementation,
  alternating). This is what produced M70's trustworthy 6.06x and 1.38x.
- **Normalize against a reference workload** measured in the same run,
  and report "reference units" alongside seconds.

### Verification

    python -m pytest tests/ -v
    cd frontend && npm test
    cd frontend && npm run lint && npx tsc --noEmit

`tests/test_docs.py` checks this file against the code: every
`api/config.py` constant named here with a value must still have that
value. It exists because three of the four such claims had gone stale
(M96). Historical values are written as "N at the time" and are
deliberately not checked.

### Further reading

- **`docs/milestones.md`** — the full milestone log (M8-present): what
  was built, every measured number, and the corrections later
  milestones made to earlier claims.
- **`docs/project-audit-2026-08-21.md`** — whole-project audit:
  redundancy findings, endpoint benchmarks, prioritized recommendations
  (all 7 resolved, M58-M65).
- **`docs/full-table-diagnostic-2026-08.md`** — the earlier engine
  diagnostic that drove M27-M34.

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

## v2 progress

The per-milestone log that used to live here is now
**`docs/milestones.md`** (M65) — one entry per milestone, M8 onward. The
narrative threads below explain the *shape* of the work; that file has
the detail, the measurements, and the corrections.

v2 grew the engine from heads-up preflop into a full-table, any-street
advisor. Two direction calls from its Phase C (postflop) design pass are
still load-bearing today and worth knowing before reading any of it:

- **Postflop works in concrete two-card combos, not the 169-class
  abstraction.** Blocker effects are a first-order postflop concern in a
  way they are not preflop.
- **Chance-node machinery was built one street at a time** — a flop-only
  tree with runouts averaged at the terminal came first, before
  multi-street chaining.

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
cap that works for the flop (`MAX_PATH_QUERY_CLASSES_PER_SIDE`, 6 at the
time — see `api/config.py` for what it is now) does
*not* carry over to the turn — `solve_flop_turn`'s steeper cost curve
turned the same cap into a 454s real request; a separately-measured,
smaller cap (`MAX_TURN_PATH_QUERY_CLASSES_PER_SIDE`, 2 at the time)
brought it back
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

**M46 update — river-level advice ships, closing this thread's last
open street — and corrects M26's own "already de-risked" claim.** M26
measured "a two-hop river walk measured ~0.002ms" and called river-
level advice already de-risked cost-wise on that basis. That number was
real but measured the wrong thing: reading a chance-branch lookup off
an *already-solved* `StrategyResult` is indeed nearly free — but the
`solve_flop_to_river` SOLVE itself, at a real derived (not the tiny
fixed 2-combo demo) range, is the actual cost, and M46 measured it
directly for the first time: 14-43s depending on combo-pool cap, far
from "de-risked." The corrected, now-actually-measured finding is
`RIVER_PATH_QUERY_MAX_COMBOS_PER_SIDE`'s own comment in M46's entry
above. What remains: an interactive flop-action wizard (still a curated
preset/fixed line, the same M24-before-M25-style gap this thread has
carried since M26), and multiway postflop solving beyond the turn
(M44's own turn-depth work is heads-up's only sibling so far; multiway
river-from-path is unscoped).

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
- **Record each milestone in `docs/milestones.md`**, not in this file —
  CLAUDE.md is loaded into context every session and holds current
  state; the log is history, consulted by search. Keep entries in the
  established voice: what shipped, the real measured numbers, findings
  and corrections, and what was deliberately deferred. If a milestone
  changes current state (a new constraint, a moved module, a new
  entry point), update the Current state section here too.
- Ship one coherent improvement per PR (matches how this project started:
  scaffold -> missing-test PR -> merge).
