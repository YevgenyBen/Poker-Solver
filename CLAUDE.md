# Poker Solver

## Current state (read this first)

A Texas Hold'em GTO solver engine plus a web UI for exploring it. The
engine is the product; the frontend is a tool for driving it.

### What it does today

Given **your hole cards, the board, the table size, and the action so
far**, it returns GTO advice for the decision you actually face — at
every street (preflop through river) and every supported table size
(heads-up, 3/6/9-max). One endpoint does this: **`POST /advise`**.

**Position is DERIVED, not supplied** (corrected M102). This line used to
say "your position" and there is no such field: the acting seat follows
from the action path, since whoever has not yet acted is the one being
advised. The wording was wrong for long enough that every probe written
against the API sent a `position` field, which silently did nothing —
requests now reject unknown fields by name rather than ignoring them.

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
| a cost cap or iteration budget | `api/config.py` (each constant carries its measurement; **`MAX_PATH_QUERY_CLASSES_PER_SIDE`, `PATH_QUERY_EQUITY_SAMPLES` and `PATH_QUERY_ITERATIONS` move together — see M131**) |
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
  jam 0.199 -> 0.032, T7s's UTG fold
  0.744 -> 0.938. **The "heads-up reference ~0.031" this used to be
  quoted against is itself unconverged (F37, M139)** — heads-up AA's
  open-jam at 100bb reads 0.0159 / 0.0040 / 0.0004 / 0.0 / 0.0 / 0.0 at
  500 / 1k / 3k / 12k / 30k / 60k iterations, so the converged value is
  **0.0** and ~0.031 is roughly a 300-iteration artifact. The
  improvement is real and large either way (0.199 -> 0.032 against a
  target of 0), but 0.032 does not "match" the reference — it is still
  0.032 above the right answer. **Exception: 9-max keeps the clamp** (`api/config.py`)
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
  `sizing_confidence` (M98). **The "fold-vs-play is unaffected" half of
  this note was wrong — corrected M110**, which measured the implied
  opening range per seat and found its own positional defect; see the
  next entry.
- **SOLVED continuation values don't fix it either (M113-M115) — the
  next attempt needs RANGE STRENGTH in the key.** M112 costed the
  architectural fix and found it affordable (15,254 preflop terminals
  collapse to **27 canonical spots** keyed on log2 SPR + live-seat
  count). M113 built the EV primitive, M114 the precompute, M115 wired it
  into `_mccfr_terminal_value` (`continuation_table=`, default `None`).
  Paired across 5 seeds at 6-max/12k: **AA jam delta +0.019 +/- 0.201,
  fell in 2/5; fold spread delta +1.23pp +/- 1.91pp**. Both null. A
  single seed looked like success (jam 0.4955 -> 0.3078, first monotone
  positional gradient ever seen) and was noise — 12k jam varies 0.37-0.92
  by seed (M110). **M98's diagnosis of the cause still stands**; this
  correction is what failed. Likeliest reason, documented in advance by
  M114: `continuation_key` carries SPR and live count but NOT range
  strength, so a 3-bet pot and a limped pot at the same SPR get the same
  value. **M116 measured how much that matters**: holding the key fixed
  and changing only the range the table is BUILT from moves values by up
  to **0.23 of the pot**, on quantities whose magnitude is 0.3-1.0 — and
  every hand correctly loses value against a tighter opponent range. So
  the key and the building range are **one fix, not alternatives**: key
  BY range strength *and* build each entry with a range of that strength.
  **Not more boards, not more iterations.** Table cost is 1,110s for 27 spots x 3 boards at a
  12-class range, dominated by `build_board_equity_table`.
- **A crude continuation term does NOT fix the sizing defect — don't
  tune it (M100).** `mccfr_solve(continuation=c, stack_bb=...)` adds
  `c * (equity - 1/n_live) * chips_behind` at terminals with money
  behind, as a cheap stand-in for the postflop game M98 showed the tree
  cannot see. Swept c = 0/0.25/0.5/1.0: AA's jam goes 0.615/0.208/0.417/
  0.374 at 12k and 0.061/0.112/0.287/0.010 at 3k — **non-monotone at both
  budgets**, so it is not capturing the mechanism. `c=1.0 @ 3,000` looks
  like a fix (0.010 +/- 0.005, tightest arm by 10x) and is not: a big
  bonus for keeping chips behind makes the all-in *dominated*, so the
  policy goes purely "never jam". (M100 called that landing BELOW the
  ~0.031 reference; against the corrected reference of 0.0 it lands
  slightly ABOVE, so that argument inverts — M100's conclusion rests on
  the sweep's non-monotonicity, which is untouched. F37, M139.)
  **The knob can produce any number, so matching the reference does not
  validate it.** A paired 9-seed test (same seed both arms, cancelling
  seed variance) gives c=0 vs c=0.25 a delta of **-0.060 +/- 0.137,
  falling in 5/9** — a coin flip. Kept default 0.0 for reproducibility;
  costs no memory.
  A real fix needs SOLVED flop continuation values. **M112 costed that
  milestone** (M100 said it could not be): 6-max has 15,254 showdown
  terminals with money behind, which looks like 8.3 hours — but they
  collapse to **27 distinct spots** when keyed on (log2 SPR, live-seat
  count), the same trick the canonical library already uses. Cost is
  near-quadratic in range width (21 combos/side 0.272s, 66 -> 2.32s,
  379 -> 58.2s), so at the existing `MAX_PATH_QUERY_CLASSES_PER_SIDE`
  it is ~10 min of offline precompute per (depth, table size), ~3 min at
  three sampled boards. **Affordable.** Unvalidated: whether a
  continuation value computed at CAPPED ranges is accurate enough to fix
  anything. Validation targets are known — AA's jam 0.615 -> ~0.03 at
  12k, and fold mass no longer flat at 0.82-0.84 across UTG/MP/CO/BTN.
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
- **The fold-vs-play call at 6-max is NOT a positional range chart
  (M110).** M98 marked the SIZING axis broken and treated fold-vs-play as
  the reliable half; a random-deal simulation measured the implied
  opening range per seat and found the reliable half has its own defect.
  Combo-weighted opening frequency, 6-max:
  | seat | shipped 3k | 12k iters | GTO approx |
  |---|---|---|---|
  | UTG | 0.281 | 0.176 | 0.15-0.18 |
  | CO | 0.316 | 0.174 | ~0.26 |
  | BTN | 0.384 | **0.159** | **~0.45** |
  | SB | 0.498 | 0.806 | ~0.80-0.87 |
  **M111 sharpened this: position is not learned AT ALL among non-blind
  seats.** At 12,000 iterations the fold mass is flat at 0.82-0.84 for
  UTG/MP/CO/BTN, and `trained_share` is 1.0 at every seat — so it is not
  under-training. M110 called it "the button opens tighter than UTG",
  over-reading a 1.7pp gap smaller than the 2.8pp CO varies between
  seeds. SB differs only because it is heads-up by then, and is correct
  there. **Same root cause as the sizing defect:** M98 showed terminals
  are priced at raw showdown equity, so playing is uniformly underpriced
  and the fold/play boundary cannot move with position. One cause, two
  symptoms — needs the architectural fix, not a bigger budget. Individual
  hands are still classified sensibly (40 random deals, zero categorical
  violations: premiums never folded, trash folded). **The SB row is NOT a
  defect** — M110 first compared it against a generic 6-max SB figure
  (~0.45); when it folds to SB the spot is heads-up vs BB, so ~0.80-0.87
  is the right reference and 0.806 is close to correct. Corrected M111,
  the same remembered-reference error M106 recorded. `SIZING_CAVEAT_REASON`
  was corrected — it used to say "trust the fold-vs-play call". **M123
  corrected it again**: it still told users "at 6-max the button has
  measured TIGHTER than under the gun", which is M110's claim and the
  one M111 withdrew in the same breath. It now states the flat result
  that was actually measured.
  Heads-up is unaffected (BTN opens 0.871, inside the 0.70-0.95 band).
- **Equity noise explains the sizing INSTABILITY, not its level (M98).**
  A 50-sample multiway equity estimate has error sd 0.091 — **+/-55bb of
  EV in a six-way 100bb pot**, worst measured 141bb — and the cache
  freezes it per key, so CFR optimizes against its own noise rather than
  averaging it out. That is why the jam frequency swings with the seed.
  `equity.py`'s own `MULTIWAY_DEFAULT_SAMPLES = 200` comment warned in
  M8 that 50 samples distorts MCCFR *via the all-in*; `api/config.py`
  overrode it to 50 on fold-rate measurements and the warning was never
  reconciled.
- **9-max is much better than M68 recorded, and "no affordable budget
  converges" was an INFERENCE, not a measurement (M157).** M68 measured
  T7s folding 12.5% under the gun at ONE budget (3,000 iterations) and
  concluded from per-iteration cost that a converging count was
  unaffordable. Nobody ran a higher one. Measured across three seeds,
  with no overlap between arms:
  | arm | T7s fold | AA jam | 72o fold |
  |---|---|---|---|
  | 3,000 + CFR+ clamp (was shipped) | .1522 / .0678 / .1450 | .81-.85 | .973-.982 |
  | **12,000 plain CFR (now shipped)** | **.8628 / .4508 / .8783** | **.06-.17** | **1.0000** |
  T7s reaches a mean **0.731** against 6-max's 0.874, where the old arm
  managed 0.122; AA stops jamming 100bb four-fifths of the time. This
  also satisfies M71's own condition - it kept the clamp at 9-max "until
  its budget can support the better rule", and at 1,333 traversals per
  seat instead of 333, plain CFR wins on every measure. **Cost 3.1x**
  (169s -> 525s), paid once per (stack, players) and pre-warmed.
  **9-max stays flagged low-confidence**: the T7s seed spread is 0.43, so
  this is materially better advice, not a converged solve, and the
  warning was rewritten because its old numbers are now false.
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
- **F42 (M146): a CACHED branch was not necessarily a TRAINED one.**
  `ensure_mccfr_chance_branch` opened with an unconditional early return
  on a `chance_data` hit, and **M75 added its `train_iterations` block
  BELOW that return** - so any branch already present was handed back
  with whatever training it had, including none. Branches come to exist
  untrained in normal operation: while one branch is trained, its own
  `chance_fn` dispatches into the next street's branches and stores them
  in the same `result.chance_data` (passed in as `chance_data=`), leaving
  a root with an all-zero `InfoSetTable`. **So SAMPLING a river card
  during turn training is what poisoned it** - the opposite of intuition.
  Measured at 3-max (Kd7c2h / Ts / 4c): 0 of 136 trained before, **50 of
  136 after**, while an unsampled card (9h) trained normally either way.
  This is the root cause of F41's "occasional" untrained node. Costs
  nothing measurable: 27.4s cold against 27.0s for a case that always
  trained. **The test is `trained_mask().any()`, NOT whether a
  `node_data` entry exists** - a first fix checked existence and changed
  nothing, because the dispatch creates that entry too.

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
- **The multiway preflop solve cache buckets stack depth by 5bb, and
  FLOORS (M124).** It is the most expensive solve in the product (66s at
  6-max, 93s at 9-max cold) and used to be keyed on `round(stack_bb)`,
  so walking depths paid it once per integer bb. `MULTIWAY_STACK_BUCKET_BB`
  now bands it, and **the solve runs at the bucketed depth** — keying on
  the bucket while solving at the real depth would hand a 99bb tree to a
  95bb player, which is F13. Adopted on a measured control, not by
  analogy to the postflop library: bucketing 4bb away moves the strategy
  no more than re-running the same solve under a different seed does.
  **Don't widen the bucket without re-running that control.** Its other
  half is worth knowing: 8-12 of 169 hands cross the fold/play line
  between two runs differing only in seed.

- **A class's frequency belongs to EVERY one of its combos, not divided
  among them (M119).** `range_from_class_frequencies` gives each
  surviving combo its class's frequency, so a class's total mass scales
  with how many combos it has. It used to divide, which meant the whole
  deck was not uniform (a suited combo weighed 3x an offsuit one, so
  AhKh was "three times as likely" as AhKs) and blockers were cancelled
  exactly (AA kept mass 1.0 whether 6, 3 or 1 combo survived). The input
  is a CONDITIONAL frequency — P(line | class), 1.0 for a position that
  has not acted — against a uniform prior over combos, so there is
  nothing to divide. **Don't "normalize" a class back to mass 1.**
  Consequence to know: `_cap_range_to_combos` now selects **round-robin
  across classes** in frequency order, because with correct weights a
  flat top-N lets the most frequent class swallow the whole budget (nine
  combos of one offsuit class at the shipped river cap). Tie-break by
  frequency alone and rely on the stable sort — adding `str(class)` puts
  22 and 32o ahead of AA.

- **The betting tree obeys the rules of poker, verified exhaustively
  (M117).** Eight legality invariants at every node of whole trees, 38
  configs: 26,354 nodes, 11,784 showdowns, zero violations. The
  load-bearing one is **no side pots at showdown** — M23 proved it from
  construction and built `query_strategy_from_path` on it; M117 is the
  first thing to check it. Two things worth knowing before touching
  `game_tree.py`: **`GameConfig` requires `stack_bb >= big_blind`**, not
  the small blind it used to (a stack between the blinds built pots
  counting chips nobody had — 96% of the pot at 0.51bb, and `/advise`
  answered 0.6bb with a confident 200); and **`_reopened_order`'s
  all-in exclusion is unreachable and kept on purpose** — no raise can
  follow an all-in under equal stacks, so it never fires, but it is the
  guard that would matter the moment stacks stopped being equal. Don't
  delete it as dead code.

- **Request models REJECT unknown fields — keep it that way (M102).**
  Every model in `api/schemas.py` inherits `_StrictRequest`
  (`extra="forbid"`). Pydantic's default of ignoring extras turned a typo
  into a confident answer to a different question: `{"hero_card": ...}`
  returned 200 with `hero: null`, and `{"player": 6}` returned 200 with
  `players: 2` — heads-up advice for a 6-max question, with nothing
  saying anything was wrong. Turning this on surfaced 22 suite failures,
  all of them tests sending a `position` field that never existed.
- **F39 (M143): the affordability guarantee was broken on EVERY turn and
  river node.** M101 restored it "at every node, on every street" and
  swept preflop and flop only. All **eleven** turn/river responses
  omitted `max_affordable_bb` outright and fell through to
  `api/main.py`'s `raw.get("max_affordable_bb",
  raw["effective_stack_bb"])` default - the pre-M101 behaviour. Measured
  at production settings, **8 of 8 turn-facing-a-bet nodes named
  `all_in:97.50` while reporting `max_affordable_bb: 85.0`**: advice for
  a bet the response itself says is unaffordable. Fixed by giving each
  response the stack entering ITS OWN street, captured before that
  street's betting reduces it (`turn_entry_stack`, `river_entry_stack`).
  **The test fixture is why the sweep could never have caught it**:
  `_disable_prewarm_and_clear_cache` sets `FLOP_TURN_RAISE_SIZES = ()`
  for speed, so under the suite the turn tree offers only check and
  all-in and no mid-street turn node exists at all. The guard
  `test_the_affordability_bound_survives_a_turn_node_facing_a_bet`
  restores one real size so the node - and the bug - can exist.
  Also seen here and left alone: after a checked-through flop and turn, a
  river node's only legal actions are check and all-in, so
  `river_action_path=["raise"]` correctly 422s. A tree-shape limitation,
  not a defect.

- **Affordability is checked against `max_affordable_bb`, NOT
  `effective_stack_bb` (M101).** The latter means three different things
  by node — preflop it is the stack net of blinds while preflop sizes are
  TOTAL commitment; at a street's opening decision it is money behind
  entering the street; one decision later it is the shortest remaining
  stack after someone bets. All defensible, none sharing a baseline with
  action sizes, so a real flop node reports `effective_stack_bb: 85.0`
  beside `all_in:97.50`. M95's "no advice names an unaffordable bet"
  guarantee was therefore only verifiable at OPENING decisions, which is
  exactly what its sweep tested. `max_affordable_bb` restores it
  everywhere: **every size in `strategy` <= `max_affordable_bb`**, swept
  at mid-street nodes too.
- **Cheap validation runs BEFORE expensive solves — keep it that way
  (M101).** A cold 6-max request with a preflop path that leaves players
  still to act used to take **76.2s to return a 422**, because the
  live-player count fetched the solve to walk a tree and the path check
  sat behind the solve. Both now build a throwaway tree: **76.2s ->
  0.1s**. The comment that justified the old version said "the preflop
  solve is already cached, so this is a tree walk" — true for the second
  caller, wrong for the first, who is the one waiting. Pinned by a test
  that counts SOLVES, not seconds.
- **F38's worst cases have a STRUCTURAL cause on the turn: the tree
  cannot express a sized re-raise (M156).** `FLOP_TURN_RAISE_SIZES=(2.5,)`
  with `FLOP_TURN_MAX_RAISES=2` gives ONE size for the first raise, so
  facing a bet the only aggressive action left is a 97.5bb shove -
  measured actions are exactly `fold / call_or_check / all_in`. The flop
  does NOT have this problem (its tree runs `(2.5, 3.0, 2.2)` at
  max_raises 4 and offers `raise:37.50` facing a bet), which is why F38's
  turn and river cases are the severe ones. **A hand that wants to raise
  a third of the pot has no such button, so the solver puts weight on the
  only one there is.**
  Adding a sized re-raise (two sizes, max_raises 3) was measured:
  | hand | shipped | with a sized re-raise |
  |---|---|---|
  | middle pair | **shove 1.000** | sized 0.576, shove 0.424 |
  | top pair | call 0.866, shove 0.134 | sized 0.348, **shove 0.475** |
  | open-ender | fold 0.996 | fold 0.996 |
  | cost | 18-20s | 28-32s (**1.5x**) |
  **Not adopted.** It fixes the indefensible case (stacking off with
  middle pair facing one bet) and worsens top pair, which is M141's
  conservation pattern on a third axis; adjudicating needs a converged
  TURN reference, and turn references are as expensive as flop ones.
  Latency is already the top complaint (M155), so paying 1.5x for an
  unmeasured accuracy change is the trade M151 declined. **The node type
  is already disclosed** - `modelled_bet_sizes` reports `[97.5]` and the
  caveat says the missing size distorts the play in both directions.

- **F38 (M142): the fold-versus-play call is NOT the sound half, and the
  caveat used to tell users it was.** Every measurement behind that claim
  was taken at a street's OPENING decision — where folding is not a legal
  action at all, because checking is free. Measured at a node FACING A
  BET against the same converged reference, 10 spots:
  | axis | mean error | worst |
  |---|---|---|
  | fold / continue | **0.1870** | 0.8017 |
  | aggression | 0.1694 | 0.5573 |
  Comparably wrong, so **"the fold call is far sounder" is withdrawn**.
  Strong hands are fine (top set, middle set and an open-ender all shove
  ~0.99 and the converged solve agrees); the failure is concentrated in
  WEAK hands facing a bet, and it is not just over-calling: **with
  nine-high (8s9s on Ac7d2h) the product recommends shoving 97.5bb
  0.5672 of the time where the correct play is to fold 0.9869.** A player
  who follows that loses a stack. Verified byte-identical across runs,
  reference identical at 1k / 2.5k / 5k iterations.
  **Why nothing caught it:** M127 judged 275 decisions CATEGORICALLY
  (premiums never folded, trash folded) — this solver does fold air
  sometimes and never folds premiums, so it passes every such check while
  folding at a quarter of the correct rate; and M138-M141's 16-spot
  sweeps all measured the opening decision, a different node.
  **Any future postflop measurement must cover nodes facing a bet**, not
  only each street's opening decision. Guarded by
  `test_the_caveat_warns_about_weak_hands_facing_a_bet`.

- **F40 (M144): the river models NO bet size except all-in, and the
  response now says so.** `FLOP_TO_RIVER_RAISE_SIZES = ()` at production
  settings, so a river node's only actions are check/call and all-in.
  Measured at each street's opening decision: flop and turn offer an
  intermediate size, **river offers none**. A player asking how much to
  bet the river cannot be answered, and without disclosure
  `all_in: 0.11` reads as "shoving beat betting half the pot" when half
  the pot was never a legal action - nothing was compared and nothing
  rejected. Every response now carries **`modelled_bet_sizes`**, and
  `BET_SIZING_COVERAGE_NOTE` is appended to the aggression caveat when
  all-in is the only way to commit chips. **Both are derived from the
  response's own rows, not from the config constants**, so they stay
  true if the constants move. Deliberately surfaced rather than
  "fixed": widening the river tree is precisely the cost that endpoint's
  budget notes say it cannot afford, and inventing a size without
  measuring it would be worse than stating what was modelled.
  **M151 measured what that costs, and it is worse than "cannot tell you
  how much".** Re-solving the same river spot with ONE normal size
  (0.75x pot) available changes the ACTION, both ways: a top pair goes
  from checking **0.9941** to checking 0.6449 and betting 0.35, and
  nine-high goes from moving all in **~0.988** of the time to betting a
  third of the pot. With all-in the only way to bet, the strategy
  collapses into check-or-shove - value hands check, bluffs jam a stack
  into a 5bb pot. (The bluff half is F38's behaviour by a different
  route: there it was RANGE composition, here the SIZE menu. Fixing one
  will not fix the other.) **Still not fixed, and the reason is
  recorded**: `solve_flop_to_river` takes ONE `raise_sizes` for all three
  streets, so enabling river sizes widens flop and turn too and that
  chain's default 20 iterations already costs 63-105s. A standalone river
  solve is cheap (~7s, and river equity is EXACT since the board is
  complete) but uses ranges that skip flop/turn narrowing - and checking
  is itself an action with frequencies, so even a checked-through line
  carries information. Trading a disclosed gap for an unvalidated model
  is not an improvement.

- **F41 (M145): `solver_confidence` knew only the TABLE SIZE, so a node
  where nothing was trained still read "high".** Measured: a 3-max river
  (Kd7c2h Ts 4c) returns **0 of 136 hands trained, every row exactly the
  uniform prior** - hero reads `0.3333 / 0.3333 / 0.3333` - while the
  response said `solver_confidence: "high"` and `range_confidence:
  fully_trained: true` for all three positions. **Two of three confidence
  signals vouched for an answer that was never computed.** It is
  OCCASIONAL, not systematic (1 of 6 measured; the rest 46-50 of ~130),
  which is worse for a user than a consistent gap - most requests look
  identical and nothing distinguishes the one that is not. `range_
  confidence` is not wrong, which is why it misleads: it describes the
  PREFLOP derivation, and those classes really were fully trained.
  `solver_confidence` now folds in `_node_is_untrained` and reports both
  reasons when a low-confidence table size ALSO applies. **Scoped to the
  unambiguous case on purpose**: one hand reading untrained is often
  benign (`trained_hands` documents zero-reach hands as untrained at any
  iteration count), so flagging that would make "low" the normal case and
  mean nothing; zero trained hands at the whole node cannot be benign.

- **F43 (M149): `trained` means VISITED, not LEARNED — and the gap put
  "fold aces to a 4-bet a third of the time" in front of a user.**
  Measured: 6-max, hero AA facing a 4-bet returns **fold 0.3333 / call
  0.3333 / all-in 0.3333** while the response says `hero.trained: true`,
  `solver_confidence: "high"`, and 101 of 169 hands trained at the node.
  F41's signal correctly stays quiet — most of the node IS trained.
  `trained_mask()` asks whether a hand accumulated any strategy_sum, i.e.
  whether it was VISITED; `current_strategy()` returns the uniform prior
  whenever every regret is <= 0, and M73 measured ~70% of rows
  all-negative, so a hand can be visited repeatedly and still average to
  exactly the prior. **Heads-up is unaffected** (BTN opens 0.998, facing
  a 4-bet jams 1.0) — this is the sampled multiway solver not reaching
  deep preflop nodes. `_hero_row_is_the_prior` now feeds
  `solver_confidence`. **Scoped to EXACT uniformity, and only when
  `trained` is true**: near-uniform is a real answer near indifference,
  and a false `trained` already fires a louder hero-specific warning.

- **Deep multiway preflop nodes are SOLVED ON DEMAND (M150) — the
  architectural fix, and the first one that improves advice rather than
  disclosing that advice is bad.** The 6-max preflop tree has **289,036
  decision nodes** and the shipped solve learns roughly the first four
  levels. Measured on the PRODUCTION cached solve, learned rows by depth:
  | depth | 0-2 | 3 | 4 | 5 | 6 | 7 | 8+ |
  |---|---|---|---|---|---|---|---|
  | nodes | 19 | 46 | 145 | 441 | 1,118 | 2,678 | ~285,000 |
  | learned | 100% | 80% | 48% | 21% | 12% | 3% | **0%** |
  Neither obvious fix applies: 285,000 nodes cannot be targeted-trained,
  and M72/M73 measured 6-max destabilising at 12k iterations, orders of
  magnitude short. So `_ensure_preflop_node_trained` borrows the pattern
  the postflop path already runs — `ensure_mccfr_chance_branch` trains a
  branch when a client asks for it. **A deep subtree is SMALL for the
  same reason it is deep: the F43 node has 10 nodes below it.** Measured
  through `/advise`: AA facing a 4-bet **0.3333/0.3333/0.3333 -> jam
  0.9999**, and trash at the same node **-> fold 0.998**, because one
  solve repairs every hand at the node (169/169 trained). 2.2s at
  `PREFLOP_DEEP_NODE_TRAIN_ITERATIONS = 200`, then cached (warm 0.07s).
  **The reach is UNIFORM and that is an assumption, not a derivation** —
  the ranges reaching a deep node are exactly what is not known, since
  its parents are unlearned too. This replaces "never computed" with
  "computed against a stated prior". Fires only on the user-visible
  defect (hero's row IS the prior, or nothing trained at the node);
  heads-up is excluded, its exact solver having nothing to fix.

- **The multiway preflop CONTINUATION fix (M112-M116) is blocked
  upstream, and F43 is the same root cause (M149).** M116's prescription
  is to key by range strength AND build each entry with a range of that
  strength. **That range does not exist at multiway.** Deep preflop nodes
  return the uniform prior, so `derive_ranges_from_path` multiplies every
  class by the same constant and composition never changes: measured at
  6-max, `BTN open / SB 3bet / BB 4bet / BTN call` yields **premium
  shares byte-identical to the 3-bet line** (BTN 0.0196, SB 0.0938, BB
  0.0332 — and 0.0332 is exactly a uniform range's premium share).
  Heads-up differentiates correctly: the 4-bettor's range is **0.883
  premium**. So the continuation table cannot be built per-spot until
  deep multiway preflop nodes are actually solved. **Don't spend another
  milestone on the table.**

- **`trained` / `range_confidence` / `source` exist because output can
  look confident and be fabricated.** Don't strip them for tidiness.
- **A repeat flop request WARM-STARTS from the cached canonical solve
  (M158) — 13-15s cold, 2.5-2.8s after.** `poker_solver/warmstart.py`
  re-keys a solved tree's `node_data` by ACTION PATH (stable across the
  tree rebuild every request does, unlike `id(node)`) and grafts it onto
  the new tree, giving hands the cached solve lacked a zero row. 50
  refinement iterations then replace 500 cold ones.
  **Justified by the right yardstick, not by zero**: hero's
  force-inclusion moves the solve less than the equity seed does (M155),
  and warm-vs-cold differs by 0.0037-0.0147 end to end against
  seed-only noise of 0.024-0.112. 25 iterations was measured and is
  worse where it counts (0.0898), so `PATH_QUERY_WARM_ITERATIONS = 50`
  is a floor, not the cheapest setting that looked fine.
  **The store is keyed on everything that changes the RANGES but not on
  hero** — sharing across heroes is the point; sharing across action
  paths serves a different question's solve, which
  `test_a_warm_cache_never_answers_a_different_question` caught when
  this was keyed on the board alone. **Only COLD solves are stored**, so
  refinements never compound on refinements. Refinement is clamped to
  the cold budget, since a caller may ask for fewer iterations than the
  warm setting.

- **The multiway flop solve SHARES its board runouts, and is ~28x faster
  (M162).** Anatomy first, per M67: a real 3-way flop solve is **99%
  equity lookups** - 427 of them at 12.7ms - against a tree of only 42
  decision nodes and 2,296 visits, and 96% of a lookup is
  `best_hand_rank_batch`. M161's fix does not transfer here:
  `_mccfr_recurse` already returns a length-N vector, so the sampled
  solver was O(N) per node already.
  The waste was that `nway_combo_equity_vector` **redraws runouts for
  every opponent tuple**, re-ranking the same candidate on fresh boards
  thousands of times. A candidate's rank on a runout does not depend on
  who it is compared against - only the comparison does. `SharedRunoutRanks`
  draws runouts ONCE per board, ranks each combo once (lazily), and
  reduces every lookup to integer comparisons. **26.3x / 31.4x / 26.3x on
  three boards, interleaved A/B in one process.**
  **The one thing that changes, and why it is sound**: shared runouts
  cannot exclude each tuple's hole cards, so colliding samples are
  DROPPED instead. That is not an approximation - rejection sampling from
  the larger deck gives exactly the conditional distribution of the
  smaller one - it costs effective sample count, ~23% at three-handed
  against the per-tuple version's ~8%. Hence `SHARED_RUNOUT_SAMPLES = 320`
  against the old default of 200: **don't lower it to 200 thinking it
  matches**, it would not.
  **Validated where it can be exact, not just where it is convenient.**
  On TURN and RIVER boards both implementations enumerate rather than
  sample, and dropping collisions leaves precisely the deck the per-tuple
  version enumerated - so they must agree to the digit, and do (0.0 over
  1,420 comparisons, zero NaN-contract mismatches). On flop boards they
  can only agree within Monte Carlo error: mean 0.020, and the controls
  say noise not bias - the reference disagrees with ITSELF under a
  different seed by MORE (0.036 vs 0.030), signed error against a
  4,000-sample truth is ~0 for both, and absolute error against that
  truth is **lower** for shared (0.0156 vs 0.0247), because 320 shared
  samples net more usable runouts than 200 per-tuple ones. Faster and
  slightly more accurate.

- **F46 (M162): multiway flop advice is NOISE-DOMINATED, and 20x the
  budget does not fix it.** Two solves of the same spot differing only in
  seed disagree by **p90 0.473, worst 1.0** at the shipped 200
  iterations. At 1,000 it is 0.449; at **4,000 - twenty times the shipped
  budget - it is still 0.313, worst 0.959.** So a multiway flop strategy
  is substantially a draw from a distribution, not a solved answer, and
  more iterations converge it only slowly.
  This was invisible before M162 because measuring it cost 5.3s per solve;
  at 0.19s the whole sweep is seconds. **It also reframes what M162's own
  speedup is worth**: the 28x buys latency, and it buys the ABILITY to
  attack this, but it does not itself make the advice better.
  **The budget was deliberately NOT raised in M162.** 4,000 iterations
  now costs ~1.5s - still 3.5x cheaper than today's 200-iteration solve -
  so it is affordable, and it does reduce noise. But "more stable" is not
  "more correct": M152 measured exactly that trap on the flop path, where
  more precision fixed hands whose true frequency was ~0 and broke the
  ones that should bet. Raising it needs a converged multiway reference
  to score against, and none exists. Changing the budget inside a
  performance milestone would also have made the speedup unmeasurable -
  the same reasoning as F45.
  Note the multiway preflop budgets (M72) are a SEPARATE constant and a
  separate finding; this is `DEFAULT_FLOP_MULTIWAY_ITERATIONS`.

- **The exact solver propagates an N-VECTOR, not an N x N matrix
  (M161) — 13.1x on a production-sized flop pool.** M160 named this as
  the remaining fix and estimated 3-5x; measured, it is larger, and the
  win grows with the pool exactly as the O(N^2) -> O(N) argument
  predicts:
  | combos | 9 | 33 | 66 | 164 | 321 |
  |---|---|---|---|---|---|
  | speedup | 1.15x | 1.28x | 1.77x | **7.56x** | **13.07x** |
  Production flop pools are ~300-330 combos. **Never slower at any size
  measured**, so there is no small-N case to guard. `_solve_recurse` now
  pushes the opponent's reach DOWN the tree and returns
  `sum_j reach_opp[j] * value(i, j)`; the step that makes it work is that
  at a node where the OPPONENT acts the branches **sum** rather than
  average, because that player's action probability is already inside the
  child's reach.
  **The replaced implementation is kept and still runnable** as
  `_solve_recurse_matrix`, with `solve(_recurse=...)` to drive it, because
  the equivalence tests are worth more than the dead weight. Don't delete
  it.
  **Equivalence is exact in float64 (1e-9 to 4.8e-15) and NOT exact in
  float32 — that is chaos, not error.** The two do the same arithmetic in
  a different order, and CFR amplifies a rounding difference through
  M74's bang-bang behaviour. Controls, all measured: each arm is
  bit-deterministic against itself (0.0); the gap grows from 0 with
  iteration count; and it collapses by ~1e9 when the same comparison runs
  in float64. Against M155's yardstick it is inside the solver's own
  noise - at the production seed over four boards, the implementation gap
  is p90 <=0.003 with 8 strategy entries over 0.05, where re-running one
  implementation under a different equity seed gives p90 0.078-0.123 and
  637-874 such entries. **The 0.194 worst case is one seed's draw** (seeds
  1-4 give <=0.001 on the same board); seed 42 is the shipped one, so it
  is quoted rather than the flattering figure.

- **F45 (M161): the solver values the second position as MINUS the
  first's payoff, and that is NOT that player's payoff once a chance node
  chains streets.** `node.pot` includes dead money carried in from
  earlier streets, so a terminal's two payoffs sum to the DEAD POT, not
  to zero - `value_a + value_b == pot - invested_a - invested_b`. Within
  a single street that offset is identical at every terminal and cancels
  out of every regret DIFFERENCE, which is why it has never mattered and
  why `solve_flop` is unaffected. Across a chance node into a street
  whose starting pot depends on how much was bet to reach it, the offset
  varies by terminal and stops cancelling. **Measured: computing the
  second player's true payoff instead moves strategies by 0.97 on a
  flop->turn tree, identically in float32 and float64** - dtype
  independence is what separates this from the rounding chaos above.
  Found because M161's first draft did compute the true payoff, and the
  chance-node equivalence test caught it.
  **Which convention is correct is NOT established, and the obvious
  answer is not safe.** The true payoff is the textbook utility, but the
  street-relative accounting has a matching gap on the FIRST player too:
  a turn subtree's terminals do not subtract that player's flop
  investment either, so switching one side alone would trade a known
  asymmetry for an unknown one. Adjudicating needs a converged
  multi-street reference, and M156 measured that turn references cost
  what flop ones do.
  **Deliberately reproduced, not fixed, in M161** (`_terminal_value_
  vector` computes in the first position's terms and negates): a
  performance rewrite that also silently changed every multi-street solve
  would make its own speedup unmeasurable and every reference solve
  incomparable. Affects `solve_flop_turn` / `solve_flop_to_river`, i.e.
  turn and river advice. Pinned by
  `test_the_vector_terminal_reproduces_the_matrix_dead_pot_convention`,
  which asserts the convention itself so a future correction has to be
  deliberate.

- **The flop solve runs in FLOAT32, and is memory-bound on N x N
  matrices (M160).** Anatomy of a real flop solve: 318 combos but only
  **16 decision nodes and 29 terminals**, 11.2ms per iteration, ~700us
  per node visit. The cost is not Python overhead and not the tree - it
  is that `_solve_recurse` carries a `num_hands x num_hands` matrix
  through every node (~809KB at 318 combos in float64), roughly **18GB of
  traffic per 500-iteration solve**. Float32 halves it: **1.12-1.32x**,
  interleaved A/B in one process per M70's rule, with worst strategy
  drift **below 5e-6**. Free because the entries are 30-sample Monte
  Carlo equity estimates whose own error is ~0.09 - float64's 16 digits
  were never carrying information. **Accumulators stay float64**
  (`InfoSetTable`): regret sums build across hundreds of iterations,
  which is where precision matters, and they are `num_hands x
  num_actions`, tiny beside the transients.
  **The algorithmic inefficiency M160 named here was FIXED in M161** -
  every node materialised N x N, making cost depth x actions x N^2. M160
  estimated the vector rewrite at 3-5x; it measured 13.07x at a
  production-sized pool. See M161's entry above.

- **The flop request's cost is 86% CFR SOLVE, not the equity table
  (M155).** M132's "the table is 41% of a flop request" is stale - its
  own 4.79x table speedup shrank the share. Measured on four random
  boards at production settings: table **14%**, solve **86%**, ~8.9s
  total for the solve leg. Through `/advise` end to end a flop request is
  8.7-20.1s, of which `query_strategy_from_path` is 5.2-16.6s (a 3x
  spread by board) and the cached preflop solve ~3.5s. **Anything aimed
  at flop latency must attack the CFR solve**; caching or extending the
  equity table caps out at 14%.

- **Hero's force-inclusion changes the flop solve NO MORE THAN THE SEED
  DOES (M155).** Adding hero's combo to a ~330-combo pool moves other
  hands' strategies by p90 0.002-0.107, worst 0.36-0.66, 1-50 hands over
  0.05. Re-running the IDENTICAL hero-free solve under a different equity
  seed moves it **as much or more**: p90 0.024-0.112, worst 0.35-0.47,
  12-53 hands over 0.05. So the per-hero cache partition pays a full cold
  solve to preserve something indistinguishable from the solver's own
  noise - and 71 of 73 flop requests in a 120-hand session were cold for
  exactly that reason. The M124 control pattern, and only askable because
  M153 fixed `equity_seed`. **M76's guard still stands** (hero must get
  an answer at all); what is now measured is that the SOLVE need not
  depend on hero. Serving an out-of-range hero from one cached canonical
  solve is the open design - it needs a way to produce hero's own row
  (best response, or a warm-started refinement) and neither is built.

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

- **F44 (M153): `equity_seed` was SILENTLY DROPPED on the production flop
  path, so every seed-variation check there proved nothing.**
  `parallel_board_equity_table` took no seed and hardcoded `DEFAULT_SEED`,
  while `solve_flop` called it as `equity_table_fn(board, combos,
  equity_samples)`. From M132 onward, injecting a builder - the shipped
  configuration - made `equity_seed` inert. **Nothing was wrong with the
  tables** (still deterministic, still correct); what broke is that a
  seed could not be varied as a convergence check, and **M138 cited "the
  seed does not move it" as evidence its reference had converged.** That
  evidence was empty. Found because two spots returned byte-identical
  values across seeds, twice each - an explanation was nearly accepted
  for that a second time before checking the call site. Fixed by
  threading the seed through both the call and the builder; guarded by
  two tests, one per half.

- **The converged reference is trustworthy for SOME spots and not
  others (M153).** Re-measured at cap 200 / 2,500 iterations across
  sample counts:
  | spot | s30 | s60 | s100 | s200 (ref) |
  |---|---|---|---|---|
  | 9s9d top set | 0.9887 | 0.9853 | 0.9889 | 0.9870 |
  | QdQh overpair | 0.0013 | **0.8614** | 0.0014 | 0.0014 |
  9s9d is stable everywhere, so the finding it carries — value hands
  need the full range, 0.549 at cap 26 against ~0.99 — is solid. QdQh
  flips wholesale at one sample count, which is **M74's bang-bang
  behaviour**: a near-tied decision where regret matching swings the
  whole strategy rather than nudging it. **Its reference is one side of a
  coin flip, and per-spot errors measured against it (including the
  0.1487 inside M138's headline mean) are not meaningful.** Check a
  spot's stability at ITS OWN settings before quoting an error for it.

- **Only the FLOP equity table is sampled; turn and river tables are
  exact (verified M154).** `remaining_needed <= 1` enumerates every
  single-card runout and ignores `samples`/`rng`. That is why
  `build_chance_node` accepts no `equity_samples`/`equity_seed` and
  `solve_flop_turn` drops `equity_samples` - every table they build is a
  turn or river board, so there is nothing to tune. M153 flagged this as
  unverified; it is now measured and pinned by
  `test_only_the_flop_table_is_sampled_which_is_why_chance_nodes_take_no_samples`,
  because if turn boards ever became sampled the chance-node path would
  silently fall back to library defaults for a quantity its callers
  believe they control.

- **A flop solve's ONE equity table is split across workers (M132), and
  the table is seeded PER ROW to make that possible.** One stream
  advancing across the upper triangle makes a pair's draw depend on how
  many pairs preceded it, so no slice can reproduce the full build;
  per-row seeding makes a row a function of the row alone, and any band
  layout merges bit-identically. 4.79x on the table, **1.34x end to end
  (flop 14.7s -> ~11s)**. `api/parallel.parallel_board_equity_table` is
  injected as `equity_table_fn` — the engine stays a plain library and
  defaults to sequential. **The pool is probed once at construction**:
  it can build successfully on a host whose workers then die (Windows
  `spawn` re-imports `__main__`, which fails from stdin), and the
  fallback was correct but printed 34 tracebacks per request.

- **The postflop budget is split three ways and the three move together
  (M131).** `MAX_PATH_QUERY_CLASSES_PER_SIDE` (26),
  `PATH_QUERY_EQUITY_SAMPLES` (30) and `PATH_QUERY_ITERATIONS` (500)
  trade against one budget, since cost is roughly (combo pairs x
  samples). The old split — 200 samples over 10 classes — bought
  precision for a range whose COMPOSITION was wrong: `_cap_range` ranks
  by how purely a class took the observed action, premiums mix, and in 5
  of 6 measured cases the raiser's modelled range held no premium hands
  at all (M130). Rebalancing cut mean error against a full-range
  reference **0.0944 -> 0.0319** and the worst case **0.345 -> 0.139**
  (both against the cap-60 anchor M138 withdrew — the REBALANCE is still
  a real improvement, since both arms were scored the same way, but the
  absolute figures are distances to a wrong target),
  at 8.4s -> 14.7s. **Width is not monotonically better** — cap 18
  measures worse than cap 10 on both — so sweep, never assume a
  direction. **The mechanism is NOT fixed**, only reduced: premiums are
  still excluded at cap 26, and the residual now leans slightly
  over-aggressive rather than passive, which is why the caveat names no
  direction. **FIVE selection-rule fixes are measured and dead — don't
  re-try them**: mass-ranking (0.775 vs a ~0.35 target — keeps only
  12-combo offsuit classes), stratifying by board CATEGORY (0.014),
  reserving 3 or 5 slots for the strongest (0.0002/0.0004 — shrinks the
  pool), and stratifying by the FULL rank tuple (M134: mean error 0.116
  against the shipped rule's 0.058, worse at both cap 10 and cap 26, so
  the coarse strength proxy was not the problem — the approach is).
  **A SIXTH is dead too, and it is the one that matters (M135)**:
  capping by COMBO budget with round-robin across classes, applied below
  the class->combo expansion via `build_library`'s `combo_cap_fn`. It
  keeps all four premiums and all 169 classes — exactly the composition
  M130 said was missing — and at matched pool size it is **2x WORSE**
  (mean error 0.111 against the shipped 0.058), non-monotone in budget.
  **So premium exclusion is a CORRELATE, not the cause**: the mechanism
  M130 described is real and still asserted by a test, but the direct
  remedy makes the advice worse, so it cannot be what drives the error.
  **Don't spend a milestone putting the premiums back.**

  **A SEVENTH died too (M136)**, and it closes the family. M135's
  round-robin gave every class the same combo count, violating M119's
  own rule that mass is frequency x combo COUNT; the corrected
  proportional version allocates combos by real mass. It is the worst
  yet — mean error 0.240 against 0.058 at a 63% larger pool, and 0.540
  at a 127% larger one. It gets monotonically WORSE with more budget,
  so "not enough combos" is not the explanation either.

  **Seven rules, all worse than ranking classes by action purity**:
  mass-ranking, stratified-by-category, reserve-3, reserve-5,
  stratified-by-full-rank, round-robin combo budget, proportional combo
  budget. They fail in both directions (0.0002 to 0.775) and three of
  them deliberately restore the diversity M130 called missing.
  **Don't add an eighth scoring function** — no reweighting or
  resampling of 169 classes into a ~330-combo budget beats the
  incumbent.

  **And don't reach for more budget either: M137 closed that too**, and
  M138 re-confirmed the decision against a corrected reference. Widening
  past cap 26 is worse on both mean and worst case, non-monotonically:
  0.1222 shipped against 0.1878 / 0.1912 / 0.1821 at caps 34 / 44 / 60.
  Width stops paying at 26, so M131's setting is an optimum rather than
  the compromise it was chosen as.

  **But every accuracy number this section used to quote was measured
  against an unconverged reference, and the real error is ~2x larger
  (F36, M138).** The "full-range reference" behind M130-M137 was a
  cap-60 solve — 60 classes of 169 — and it does not hold still: a
  flopped set on 2h6d9c reads **0.381 / 0.5948 / 0.9186 at caps 60 /
  100 / 200**, and 0.987 once the uncapped solve gets 2,500 iterations.
  The uncapped solve IS trustworthy (doubling equity samples moves it
  0.9186 -> 0.9206; the seed does not move it), so the true answer is
  ~0.99 and the old anchor was off by 0.6. **The shipped config's real
  error is mean 0.1394 / worst 0.8810, not the 0.058 / 0.168 this file
  claimed** — and 0.058 was never a floor, it was a distance to a wrong
  target. (M138 first put this at 0.1222 / 0.4381 on five spots; M140
  widened to sixteen and it grew. **Each widening has made the defect
  look larger, so treat these as lower bounds.**)

  **M140 found the one case specific enough for a user to act on:
  OPEN-ENDED STRAIGHT DRAWS are over-bet, 3 of 3, by +0.170 to +0.881.**
  The worst is 7h8h on 2h6d9c, where the product recommends a 2.5x-pot
  bet **0.88 of the time while the converged solve checks 100%** —
  reproducible byte-identical, reference converged at that spot. It is
  also incoherent WITHIN the class: 7h8h / 8h7d / 7s8s are the same 78
  open-ender with near-identical true frequencies (0.0001-0.0037) and
  ship 0.8811 / 0.4142 / 0.1720 — 5x apart on suits alone, ranked in the
  OPPOSITE order to the truth. Gutshots and a no-draw control are clean,
  so the caveat says open-ended, **not "draws"** — over-generalising
  would be M110's error mirrored. Direction elsewhere is stated as
  measured, not as it would be most useful: error tracks the TRUE
  frequency on all 16 spots. The tempting summary "we under-bet your
  strongest hands" is FALSE across boards — middle set flips sign
  between 2h6d9c (0.594 true, under) and Ac7d2h (0.001 true, over).

  The user-facing caveat understated the worst case 5x and is
  corrected; `POSTFLOP_AGGRESSION_ERROR_MEAN`/`_WORST` now record the
  measurement, pinned to the copy by
  `test_the_aggression_caveat_quotes_its_own_measurement`.
  **Why eight attempts missed it:** all seven selection rules chose
  classes WITHIN a cap, so they shared the reference's blind spot and
  nothing disagreed. Bucketing broke the symmetry by seeing all 169
  classes — it looked like it was failing, and part of what it was doing
  was disagreeing with a wrong answer. **Any new accuracy claim here
  must state what reference it used and show that reference converged.**

  **Representation is dead too — that was the last untried idea (M138).**
  Bucketing strategically similar hands so a fixed budget describes the
  range's SHAPE instead of sampling from it. M17's machinery cannot be
  reused: both halves anchor on the full N x N combo table
  (`compute_combo_strengths` builds it just to derive the bucketing
  signal), so bucketing all 169 classes needs a ~1,176 x 1,176 table,
  strictly more than what ships. Built the variant that avoids it —
  cheap O(N) bucket assignment plus bucket-vs-bucket equity from SAMPLED
  member pairs, cost B^2 x k independent of N — with two signals
  (five-card rank; equity against a reference subset, O(N x R)) and four
  bucket counts each. **Every arm is worse: 0.085-0.127 against the
  shipped 0.058 on the old anchor, ~0.20 against 0.1222 on the corrected
  one, at 2-8.5x the cost, non-monotone in bucket count.** It fails by
  collapsing aggression specifically — equal-mass bucketing puts top set
  and an overpair in one bucket (little mass lives at the top of a
  range), and averaging equity inside a bucket destroys the spread that
  value-betting monetises. **Nine ideas are now measured and dead.**

  **M141: they all failed for ONE reason — the cap MOVES error between
  hand types instead of reducing it.** Measured on the 16-spot set with
  samples and iterations fixed, so the cap is the only variable:
  | group | cap 26 | cap 34 | cap 44 |
  |---|---|---|---|
  | made hands (sets/pairs) | **0.1052** | 0.2022 | 0.2458 |
  | draws | 0.2924 | **0.0031** | 0.0784 |
  | air / overcards | 0.0080 | 0.0018 | 0.0047 |
  Made-hand error grows monotonically with width while draw error
  collapses, so mean error stays in a narrow band while its COMPOSITION
  shifts. Every one of the nine was a single-knob reweighting of the same
  169 classes into a fixed budget — each could only redistribute error,
  never remove it, which is why nine structurally different rules all
  landed in 0.09-0.14. **Not a floor: a conservation law.**
  Cap 34 wins the raw 16-spot mean (0.0899 vs 0.1394) and is deliberately
  NOT adopted — excluding the single worst draw flips it, it wins 8/16
  and loses 6/16, its worst case is worse (0.7635 vs 0.4381), it costs
  2.7x, and it damages top set most (0.2235 against a true 0.987).

  **M152 confirmed the same law on a SECOND axis: PRECISION is dead too.**
  Nobody had isolated it — M137 held samples at 30 and moved the cap,
  M138's reference moved cap/samples/iterations together, so the error was
  attributed to the cap by default. Holding the cap at 26 and raising
  precision gives mean error 0.2992 (shipped s30/it500) -> 0.1840
  (s200/it500) -> **0.1268** (s30/it2500) -> 0.1752 (s200/it2500) —
  non-monotone, and per spot the same trade: **more precision pushes
  everything toward betting less**, fixing hands whose true frequency is
  ~0 (open-ender 0.881 -> 0.055) and breaking the ones that should bet
  (top set 0.438 -> 0.476, middle set 0.016 -> 0.098). 3 of 5 better, 2 of
  5 worse — cap 34's signature exactly. **Nothing yet moves top set toward
  its true 0.987.**

  **And the metric is biased: MEAN ERROR DEPENDS ON SPOT-SET
  COMPOSITION.** Three of those five spots have a true value near zero, so
  any change that reduces aggression improves the mean without improving
  the advice. That is why iterations appear to halve the error, and why
  cap 34 won M141's raw mean — both sets are dominated by low-true spots.
  **Report per-hand-type error here, never a mean over an unbalanced
  set**; two milestones have now nearly adopted a config change on the
  strength of one.

  Untested hypothesis that fits all seven: **coherent-but-narrow beats
  diverse-but-thin.** ~26 classes at near-full frequency look like a
  consistent set of holdings; 169 classes at one to four combos each
  have the right diversity but are a smear no real opponent holds.
- **Cache ceilings are sized by BYTES, not just entry count (M127).**
  "Every cache is bounded" (M93/M104, re-verified M124) bounds the entry
  COUNT — and entry size varies 180x, so it never bounded memory. Found
  by PLAYING 120 hands, not by inspection: the working set grew linearly
  at **1.4 MB/s with no plateau** (1,642 -> 4,244 MB). `river_path` held
  **38.45 MB per entry** at a ceiling of 128 — a 4.9 GB cache alone; four
  caches were over, 6.4 GB combined. Ceilings now derive from measured
  entry cost against `MAX_CACHE_BYTES_PER_CACHE` (160 MB), and
  `test_cache_ceilings_are_sized_against_what_an_entry_actually_costs`
  measures a real entry rather than trusting a comment. Re-run after the
  fix: **0.029 MB/s, plateau at ~997 MB.** **Don't raise a maxsize
  without re-running that test** — the expensive postflop entries are the
  ones that bite.

- **EVERY solve cache is BOUNDED (M93, completed M104) —
  `_SolveCache(name, maxsize=N)` with LRU eviction.** Nothing evicted
  anything before M93, so heap grew ~0.065 MB per request with no
  ceiling. M93 left `_multiway_cache` and `_preflop_raw_cache` unbounded
  "because the pre-warm fills them" — but both are keyed on
  `round(stack_bb)`, which comes from the REQUEST, so a client walking
  depths minted an entry each (0.152 MB of heap per depth). Now 64 and
  128. `multiway_equity` is the one deliberate exception, keyed by a
  config constant rather than request input. **Don't add an unbounded
  cache** — `test_no_solve_cache_is_unbounded` asserts over every
  module-level cache.
  **`_SolveCache.lock` is an RLock deliberately**: call sites hold it
  across read-check-write and call `store`/`get` from inside. A plain
  Lock self-deadlocks — it hung the whole suite once.
- **Expensive solves go through `_SolveCache.get_or_compute`, not
  check-then-compute (M92, and `_get_or_solve_preflop_raw` finally in
  M104 — it was missed the first time, costing 8 real preflop solves for
  8 concurrent cold requests where 1 was needed).** The old pattern let N concurrent misses on
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
- **`docs/audit-2026-08-23.md`** — the latest whole-project audit
  (M101): static checks, live-play simulation against the real API, and
  cold-vs-warm benchmarks. Four findings, all acted on.
- **`docs/project-audit-2026-08-21.md`** — earlier whole-project audit:
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
