# Wide benchmark — 2026-09-16 (M260)

Fifth whole-product benchmark. The last one, M252, measured one thing:
how often a player MEETS each disclosed defect. This one runs two
halves, and the grades for both were written down before either ran.

1. **Against ourselves:** a wide `/advise` population, the tree walked
   under two raise weights and three seeds each. It measures speed,
   correctness and exposure, meaning how often each warning fires. It
   extends M252 in two ways:
   - it records every advisory note;
   - postflop, it asks the node FACING a bet at a size the response
     itself offers. M252 always sent the 2.5× overbet.
2. **Against the independent solver:** every street and both node
   types, on fresh boards.
   - **Rows:** each reference serves the opening decision and the node
     facing each of our three bet sizes.
   - **Regret:** river and turn are priced in chips, as regret inside the
     reference's game, on the configuration M258/M259 validated. The
     turn dump carries its river (F62).
   - **Frequency:** the flop is measured in frequency only. Its regret
     needs a three-round dump, which is 3.5 GB to read at this depth
     (F67).

## Grades — fixed before the data

### External, per cell (street × line × node type)

| grade | regret, % of pot (turn, river) | frequency distance (TVD, all streets) |
|---|---|---|
| **A** | under 0.5% | under 0.05 |
| **B** | under 1.0% | under 0.10 |
| **C** | under 2.0% | under 0.20 |
| **D** | under 4.0% | under 0.35 |
| **F** | 4.0% or more | 0.35 or more |

Rules for reading them:

- **Where both measures exist, the regret grade is the cell's grade.**
  The project's thesis is money, not frequency (M182/M183). The TVD
  grade is reported beside it, and disagreement between the two is a
  finding.
- **Regret is an upper bound** (M258). A cell's grade can therefore be
  too harsh, but not too kind, *if its controls pass*:
  - **the spot's root control** (`bench.dump_control`, reach 1);
  - **remapped mass:** a cell averaging over 0.5 is graded
    "mapping-dependent" instead of by letter.
- **A row is graded only if its node is REACHED** in the reference's own
  game: the villain takes the line to it at least **5%** of the time.
  - **Why:** the ε bound at a node reached with probability p is ε/p
    (M259). The smoke run shows the effect: facing a 2.5× overbet at
    p ≈ 0.003, the reference's own slack was 13 bb.
  - **Selection bias:** the reach fraction is a property of the
    reference alone, so selecting on it cannot favour our answer.
  - **Excluded rows** are counted and reported, not graded.
  - **Check:** each graded cell also gets its regret net of the
    reference's own slack.
  - *(Added after the one-spot smoke run and before the main run; no
    graded number existed yet.)*
- **Frequency on the flop is graded on TVD alone** and marked as such.
  M221/M242 measured the flop as the street closest to the reference,
  so a flop grade below B would contradict two studies and must be
  explained before it is believed.

### Self, per axis

| axis | A | B | C | F |
|---|---|---|---|---|
| **defects** (unaffordable size, non-distribution, uniform row at high confidence, premium folded preflop) | 0 | — | — | any |
| **latency**, share of decisions over 5 s | under 1% | under 3% | under 10% | 10% or more |
| **refusals** (non-200 on a legal, tree-walked path) | 0 | under 0.5% | under 2% | 2% or more |
| **honesty**: a signal that cannot vary (F51's shape) | none | — | — | any new one |

Latency is quoted in seconds AND in drift-normalised units (M240), with
the run's measured drift printed beside it.

## Scorecard

| area | grade | one line |
|---|---|---|
| **Defects** (self) | **A** | 0 in 2,293 decisions, and 0 in the 393-decision recheck |
| **Latency** (self) | **A** | none over 5 s; p50 0.42–0.46 s, p90 2.2–2.3 s, worst 4.86 s |
| **Refusals** (self) | **C → A** | 32 flop facing-a-bet requests refused (1.3–1.45%); fixed, recheck 0 |
| **Honesty** (self) | **F → fixed** | a signal that could not vary, and a note that was false on 62% of firings |
| **River, opening** | **A** (0.41% of pot) | frequency C (TVD 0.197); the gaps are cheap |
| **River, facing a bet** | **B** (0.88% of pot) | frequency C (0.153); under-folds at 2.16σ |
| **Turn, opening** | **B** (0.71%) | n=71 on 24 boards; frequency D (0.300) |
| **Turn, facing a bet** | **C** (1.20%, ±0.22) | n=130 on 24 boards; 17 pure-node rows priced separately (E5) |
| **Turn, shoving facing a bet** | **F** (16% of pot) | 2.41 bb a decision at 5.42σ, a floor; now disclosed (E5) |
| **Flop, opening** | **F by TVD, B by kind** | bets as often as the reference (0.067); disagrees on WHICH size (0.418) |
| **Flop, facing a bet** | **D** (TVD 0.256) | a kind-level disagreement with no direction; unpriced |

Grades follow the rubric above. Where a cell has a regret grade, that
grade leads.

---

## Half one — against ourselves

Two arms, raise weight 3.0 and 1.0. Each ran three seeds of 120 hands,
with multiway depths prewarmed and every request timed for drift.

| arm | decisions | p50 | p90 | worst | over 5 s | drift |
|---|---|---|---|---|---|---|
| weight 3.0 | 1,159 | 0.464 s (1.77 u) | 2.207 s (8.22 u) | 4.529 s | 0 | **2.51** |
| weight 1.0 | 1,134 | 0.424 s (1.59 u) | 2.312 s (8.83 u) | 4.864 s | 0 | 1.17 |

**The machine slowed 2.5× late in the first arm** while nothing else was
running. So seconds are quoted beside drift-normalised units (M240).

### Exposure — what a player meets

| signal | weight 3.0 | weight 1.0 |
|---|---|---|
| multiway postflop not reproducible (M245) | 23.0% | 23.8% |
| `river-under-fold`, all decisions | 1.73% | 2.03% |
| `river-under-fold`, river facing a bet | 27.4% | 27.1% |
| `turn-independent` | 5.4% | 11.5% |
| `street-isolation` | 4.7% | 10.5% |
| `costly-band` | 7.4% | 6.4% |
| two-live preflop, weak hands (M251) | 1.21% | 0.62% |
| uniform row / untrained hero row | 0.09% each | 0 |

- **Multiway irreproducibility is still the most-met warning**, as it
  was in M252 (20%).
- **The under-fold note fires on 27% of river facing-a-bet decisions**,
  against M243's design figure of 21%.

### W1 — the heads-up flop published an all-in the player could not use

**Fixed.**

- **The defect:** at 100bb the flop answered `all_in:95.00` beside
  `max_affordable_bb: 97.5`. The tree the next request walks offers
  `all_in:97.50`, so the API rejected its own label with a **422**.
- **The cause:** the canonical library solves at a depth rounded DOWN
  (F13).
- **Its reach:** all 32 of the benchmark's refusals.
- **Why nothing caught it:**
  - M101's invariant (every size ≤ `max_affordable_bb`) held throughout.
  - The round-trip test skipped `all_in` names.
- **The fix:** `_all_in_at_the_real_stack` relabels the action on a copy
  of the shared entry. The solve is unchanged.
- **Verified:** two new tests, both mutation-checked. The recheck seed
  that had 7 refusals now has 0.

### W2 — `sizing_confidence` was a constant postflop

**Fixed.**

- **The defect:** "high" on **1,468 of 1,468** postflop decisions.
- **Why it mattered:** that included responses whose own
  `bet-sizing-coverage` note said no intermediate size existed. One
  response told a client both to trust the sizes and not to. This is
  F51's shape, in the field next to the one M252 fixed.
- **The fix:** "low" exactly where that note fires.
- **Verified:** the recheck agrees on 26 of 26 decisions.

### W3 — the bet-sizing note blamed the model for a short stack

**Fixed.**

- **The defect:** **189 of 303** firings were opening decisions where a
  third of the pot already exceeded the stack, mostly 20bb after a
  four-bet. The note said a smaller bet "was never available" and that
  this "distorts the PLAY". That's false there, because the real game
  has no smaller bet either.
- **The fix:** the note is silent on those decisions now.
- **Still fires facing a bet:** there, the tree's single re-raise
  multiple not fitting in the stack is a real limit of the model.

---

## Half two — against the independent solver

42 references: river 28 (16 three-bet, 12 single-raised), flop 8, turn 6.

- **Convergence:** 0.24–0.50% of pot.
- **Controls:** all 34 root controls pass. Turn ratios run 0.33–2.51.
- **Unreached rows:** 96 fall below the pre-registered 5% reach rule and
  are excluded.
- **Solve time per reference:** river 4 s, turn 15 s, flop 511 s.
- **Pricing time:** **a turn spot took 25–33 minutes to price.** The walk
  over a 140-class pool dominates.

### Pooled, reached rows

| street | node | n | TVD | regret, % pot | ±sem | net of slack | median | worst |
|---|---|---|---|---|---|---|---|---|
| river | opening | 43 | 0.197 | **0.41** | 0.12 | 0.20 | 0.07 | 4.39 |
| river | facing | 95 | 0.153 | **0.88** | 0.14 | 0.51 | 0.20 | 5.83 |
| turn | opening | 5 | 0.298 | **0.68** | 0.24 | −0.05 | 0.65 | 1.51 |
| turn | facing | 17 | 0.274 | **2.19** | 0.80 | 1.03 | 0.31 | 10.56 |
| flop | opening | 8 | 0.418 | — | — | — | — | — |
| flop | facing | 32 | 0.256 | — | — | — | — | — |

### Per cell

| street | line | cell | reached / rows | TVD | regret % | grade | remapped |
|---|---|---|---|---|---|---|---|
| river | 3-bet | opening | 19/19 | 0.275 | 0.17 | A | 0.00 |
| river | 3-bet | facing 0.33× | 32/32 | 0.178 | 0.82 | B | 0.00 |
| river | 3-bet | facing 0.75× | 26/32 | 0.170 | 0.75 | B | 0.00 |
| river | 3-bet | facing 2.5× | 8/32 | 0.023 | 0.47 | A | 0.00 |
| river | SRP | opening | 24/24 | 0.136 | 0.60 | B | 0.22 |
| river | SRP | facing 0.33× | 19/24 | 0.131 | 0.87 | B | 0.25 |
| river | SRP | facing 0.75× | 6/24 | 0.130 | 0.91 | B | 0.01 |
| river | SRP | facing 2.5× | 4/24 | 0.237 | 3.13 | D | 0.00 |
| turn | 3-bet | opening | 5/5 | 0.298 | 0.68 | B | 0.00 |
| turn | 3-bet | facing 0.33× | 8/8 | 0.284 | 1.23 | C | 0.00 |
| turn | 3-bet | facing 0.75× | 6/8 | 0.275 | 3.40 | D | 0.00 |
| turn | 3-bet | facing 2.5× | 3/8 | 0.249 | 2.32 | D | 0.00 |
| flop | 3-bet | opening | 8/8 | 0.418 | — | F (TVD) | 0.00 |
| flop | 3-bet | facing 0.33× | 14/16 | 0.340 | — | D | 0.00 |
| flop | 3-bet | facing 0.75× | 16/16 | 0.185 | — | C | 0.00 |
| flop | 3-bet | facing 2.5× | 2/16 | 0.232 | — | D | 0.00 |

The single-raised pot's remapped mass (0.22–0.25) is **F65's rounding at
a 5 bb pot**, not a menu difference. The reference offers `BET 2`
against our 1.65, which is 21% apart and over the 5% tolerance.

### E1 — the river is the product's best street, in chips

- **The price:** 0.41% of pot at the opening decision and 0.88% facing a
  bet, an A and a B, on the street where equity is exact.
- **Frequency looks worse than money:** TVD is 0.15–0.20, which is
  M183's pattern again.
- **The under-fold replicates on fresh boards:**
  - signed fold gap facing a bet: −0.0457 at 2.16σ;
  - where the note fires: **1.55% of pot**, fold gap −0.088;
  - where the note is silent: **0.65%**, fold gap −0.031.
- **Against M259:** M259 measured 1.79% against 0.44% on a different
  population. The note's price holds up on boards it never saw.

### E2 — the turn facing a bet, first pass (superseded by the n=24 run below)

- **The price:** 2.19% of pot ±0.80 over 17 rows; 1.03% net of the
  reference's own slack. The worst row is 10.6%. With R10's off-support
  rule applied, it is 1.39% over 15 rows.
- **It is thin:** six boards, because a turn spot costs half an hour to
  price.
- **It fits the record:** everything the project knows about the turn
  (M222–M236) points the same way.
- **It is not published to players.** Quoting a 17-row figure would
  repeat M232's error. It stays a finding, and R4 is what would make it
  publishable.

### E3 — the flop disagrees about SIZE, not about whether to bet

- **The flag:** the opening cell's TVD of 0.418 is an F, and the rubric
  required explaining that before believing it.
- **The explanation:** collapsed to fold / passive / aggressive, the same
  rows read **0.067**.
  - The two solvers bet equally often: gap −0.067 at 1.46σ.
  - They choose different sizes. AhAc bets 0.97 in both, split across
    sizes differently.
- **Earlier flop work stands.** M221 and M242 measured aggression and
  folding, never size, so "the flop is the closest street" still holds.
  This is the first measurement of the size choice.
- **Facing a bet is different:** a kind-level disagreement (0.250) with
  no net direction.
- **Neither is priced:** the flop's regret needs a three-round dump
  (F67).

### E4 — the turn note overstated the typical gap about three times

**Fixed.**

- **The trigger:** the benchmark's in-range turn heroes read the
  OPPOSITE direction to `TURN_INDEPENDENT_NOTE`, −0.15 on five rows.
- **The recheck:** five rows settle nothing. So M236's 24 turn references
  were re-scored against **144 heroes drawn by range weight**, with the
  reading rule written first.

| heroes | n | median gap | within 10 pts | signed |
|---|---|---|---|---|
| M236 hand-picked | 21 | 0.4367 | 2 | +0.2484 (3.29σ) |
| **range-weighted** | 144 | **0.1816** | **47** | **+0.1860 (6.46σ, 21 of 24 boards)** |

- **The direction stands:** split-half 5.00σ and 5.86σ. The five-row
  reading was noise.
- **The size was a property of the hero list:** hand-picked hands were
  closer decisions than a typical hand from the range. That's M243's
  river finding, one street earlier.
- **The note now leads with what a range-weighted hand meets:** a gap of
  18 points, a third within 10, and betting more by 19. The hand-picked
  53 stays only as labelled context.

### Instrument notes

- **Missing heroes.** 40 heroes had no row in their reference.
  - **The cause:** the reference drops classes weighted under about
    0.006 (present at ≥ 0.0068, absent at ≤ 0.005), which is consistent
    with two-decimal parsing.
  - **The size:** **0.13% of range mass** on average and 0.21% at worst
    (0.17% in the M259 references). That can't move a result.
  - **The one visible effect:** it emptied one turn spot, whose heroes
    were drawn uniformly from nonzero combos. Heroes should be drawn by
    weight (R5).
- **River direction depends on the pot type.** The opening aggression gap
  is **−0.176 (2.38σ)** in three-bet pots and **+0.084 (2.26σ)** in
  single-raised pots. Neither note claims a river direction, so nothing
  is published.

---

## Recommendations

| # | recommendation | status |
|---|---|---|
| R1 | Name the flop's all-in at the real stack (W1) | **DONE** — 2 tests, recheck 0 refusals |
| R2 | Let postflop `sizing_confidence` read the rows (W2) | **DONE** — test in both directions |
| R3 | Silence the coverage note where the stack, not the model, removes the size (W3) | **DONE** — test |
| R4 | Make the turn walk cheaper, then price the turn at n ≥ 24 | **DONE** — walk 36.9× faster; 24 boards priced (below) |
| R9 | Disclose the turn shove facing a bet (E5) | **DONE** — `TURN_SHOVE_NOTE`, 3 tests |
| R10 | Grade regret only where off-support mass ≤ 0.5, and price pure nodes by action value | **DONE** in the report script; recorded in CLAUDE.md |
| R5 | Score references against RANGE-WEIGHTED heroes, never hand-picked or uniform | **DONE** for the turn note (E4); recorded in CLAUDE.md as the rule |
| R6 | Correct `TURN_INDEPENDENT_NOTE` to the range-weighted figures (E4) | **DONE** |
| R7 | Price the flop's size disagreement | OPEN — needs three-round dumps (3.5 GB each, F67) or a streaming reader |
| R8 | Report river direction by pot type in any future river copy | recorded; nothing to change today |

**R4 would unlock the most.** The turn's price is the largest unknown
left, and the cost is in our own walk, not in the solver.

### R4, acted on — the walk

`LeafEquity` built the full (N+1)×(N+1) equity table for every river
board, then read only row 0.

- **The fix:** on a board with at most one card to come, hero's row is an
  O(N) rank comparison.
- **Correctness:** equal to the table's row exactly, pinned by a
  parametrised test.
- **Speed:** one turn row at a 584-combo pool went **228.8 s → 6.2 s
  (36.9×)**, with the same regret to 1e-9.
- **What that buys:** a turn spot now prices in ~75 s with 12 rows,
  instead of 30 min with 4–7.

### R4, acted on — the turn priced at n = 24

The rule was fixed before the run:

- **Design:** 24 fresh three-bet turn boards at cap 140, with heroes drawn
  by range weight (R5): three at the opening decision, three per facing
  size. Same controls and same 5% reach rule as above.
- **Reading, turn against river, per node type:**
  - turn larger at 2σ → the turn is the costlier street in chips;
  - otherwise → the streets are not separable.
- **Quoting the turn's price to players requires both:**
  - its cell is separable from zero at 2σ;
  - all spots pass the root control.

  Otherwise it stays a report finding.

**Result.** 24 of 24 root controls pass, exploitability 0.32–0.50%, no
missing heroes, 219 reached rows.

**A rule applied after seeing the rows, and disclosed as such.**

- **The problem:** the first read gave turn facing a bet **7.4% of pot,
  worst 231%**. Those extremes were pure-node disagreements (M258).
  - Our row sat 99.98% on an all-in the reference never plays.
  - Regret was computed on the 0.01% of our row that overlapped, then
    renormalised.
- **The fix:** CLAUDE.md already states the rule: report off-support
  mass, and at a pure node ask the frequency question. So regret is now
  graded only where off-support mass is 0.5 or less.
  - **Excluded here:** 17 facing rows and 1 opening row.
  - **Excluded in the first external set:** 2 turn rows.
  - **Recomputed:** the tables above were recomputed on the same basis.
    The first set's turn facing cell moves from 2.19% to **1.39%**, and
    nothing on the river changes.

| cell | reached | priced | regret % pot | ±sem | net of slack | median | grade |
|---|---|---|---|---|---|---|---|
| opening | 72 | 71 | **0.71** | 0.09 | 0.13 | 0.58 | **B** |
| facing 0.33× | 72 | 63 | 1.21 | 0.34 | 0.81 | 0.29 | C |
| facing 0.75× | 48 | 44 | 1.22 | 0.36 | 0.17 | 0.09 | C |
| facing 2.5× | 27 | 23 | 1.15 | 0.45 | 0.47 | 0.05 | C |
| **facing, pooled** | 147 | 130 | **1.20** | 0.22 | 0.53 | 0.15 | **C** |

**By the rule, the turn facing a bet is the costlier street.**

- **Numbers:** 1.20% ±0.22 on the turn against the river's 0.88% ±0.14,
  a difference of 0.32 at **1.23σ**.
- **Verdict:** that is not separable, so the streets are **not
  separable** facing a bet.
- **Turn opening decision:** 0.71% against the river's 0.41%, +0.30 at
  1.99σ, just short of the bar. Not separable either.
- **Copy:** no turn price for the regret cells goes into player-facing
  copy, since the rule asks for a comparison that did not clear. The
  pure-node rows are a different matter (E5).

### E5 — facing a turn bet, the engine shoves where the reference never does

**The most expensive disagreement measured here. Disclosed.**

**The rows.** The 17 excluded rows are one behaviour:

- **Ours:** with a strong made hand facing a bet (a set, trips, two
  pair), we move all-in about 99.98% of the time.
- **The reference:** it calls, or raises small to 10, and shoves under
  1% of the time.
- **Example:** 6h6c on Kh6s2hJd facing a third-pot bet shoves 92.5 bb
  into a 20 bb pot **0.9967**. The reference raises small **1.0**.

**The price.** Regret cannot see a pure node, so the shove was priced
directly, in the reference's game: the value of the best supported action
minus the value of the all-in.

| rows | boards | mean | median | sem | negative |
|---|---|---|---|---|---|
| 17 | 9 | **2.675 bb** | 2.525 | 0.517 | **0** |

**The gate.** It fires on the turn, heads-up, facing a bet, when the row
is at least 50% all-in and the street opened at SPR ≥ 5. Every row it
fires on:

- **Rows:** 21 in all, the 17 plus 4 where the reference does shove a
  little, priced by regret.
- **Cost:** **2.408 bb = 16% of the pot, 5.42σ.**
- **Precision:** the reference never shoved on 17 of the 21 (81%).

**A floor.** The reference never trains its villain's reply to a shove it
doesn't make. That villain folds 45–83% to it, and M258 measured such
branches as flattering the shove.

**Exposure.** In the self-benchmark, 3 of 55 heads-up 100 bb turn
facing-a-bet decisions shove with at least 0.9 of the row. So this is
rare, and about 2.4 bb each time.

**Mechanism, a hypothesis.** The turn is solved without playing the
river:

- a call is valued at showdown equity and collects no river value;
- a shove collects its value now.

This is M222's street-isolation story in its sharpest form, and
M223/M232 already showed configuration cannot reach it.

**Shipped.** `TURN_SHOVE_NOTE` (`turn-shove`) says so, quotes the floor
and names calling as the alternative. Four tests pin it: every gate
condition is removed once, and the copy is pinned to its constants.

---

## Can a live player use this today?

Built only from the measurements above. Nothing new was solved for this
section except one extra cut of the external rows: **how often our
recommended action matches the reference's**, collapsed to fold /
check-call / bet-raise.

### The short answer

- **Heads-up, turn or river:** yes, as a strong guide.
  - It picks the same kind of action as an independent solver on
    **79–88%** of decisions, and **86–97%** where that solver is clear
    about it.
  - The per-decision price of following it is about **0.03–0.18 bb**.
- **Heads-up flop:** usable for WHETHER to bet, less so for how much.
  - **Whether to bet:** it agrees with the reference 88% of the time at
    the opening decision.
  - **How much:** it usually picks a different size, and the cost of that
    is unmeasured.
- **Multiway (3+ players past the flop):** not something to lean on.
  - About one decision in four there carries a "not reproducible"
    warning.
  - No outside reference exists to say how accurate it is.
- **Heads-up, speed is not the constraint.**
  - Every one of 2,293 benchmark decisions came back inside 5 seconds.
  - Facing a bet with nothing cached, it takes 1.9–2.8 s (median).
- **Multiway, the FIRST request at an unusual stack can take minutes.**
  - Measured: 6-max at 73 bb waited **99.5 s**, 3-max at 37 bb **51.9 s**.
  - Only 100/50/20 bb are prewarmed, and the benchmark only drew those
    stacks, so it never saw this (see *Speed*).

### Accuracy — how good is the read?

**Agreement with the independent solver**, reached rows only, heads-up
3-bet pots at 100 bb (the river also in single-raised pots):

| decision | rows | same kind of action | when the reference is clear (≥ 80%) |
|---|---|---|---|
| river, first to act | 43 | **88%** | **91%** (32 of 35) |
| river, facing a bet | 95 | **85%** | **97%** (64 of 66) |
| turn, first to act | 72 | **81%** | **97%** (37 of 38) |
| turn, facing a bet | 147 | **79%** | **86%** (95 of 110) |
| flop, first to act | 8 | 88% | 88% (7 of 8) |
| flop, facing a bet | 32 | **72%** | **74%** (23 of 31) |

**Read it this way:**

- **Where the right play is clear, the engine usually names it:** 86–97%
  on the turn and river.
- **Most remaining disagreement is at close decisions,** where either
  action is worth about the same. That is why the chips below are small.
- **The flop facing a bet is the weak spot** in this table: roughly one
  clear decision in four disagrees, and it is not priced.

**What following the advice costs.** This is the most a decision can
lose against an opponent playing the reference's strategy. It is an
upper bound, not an expected loss.

| decision | mean | median | worst tenth starts at | pot |
|---|---|---|---|---|
| river, first to act | **0.03 bb** | 0.01 | 0.07 | 5–15 bb |
| river, facing a bet | **0.10 bb** | 0.02 | 0.28 | 5–15 bb |
| turn, first to act | **0.11 bb** | 0.09 | 0.20 | 15 bb |
| turn, facing a bet | **0.18 bb** | 0.02 | 0.51 | 15 bb |
| turn, **shoving** facing a bet | **2.4 bb** (a floor) | 2.3 | — | 15 bb |

**The shape matters more than the mean.**

- **Most decisions cost almost nothing:** medians are 0.01–0.09 bb.
- **The cost sits in a tail you can recognise,** and the response flags
  most of it:
  - the turn shove facing a bet (`turn-shove`, about 2.4 bb each time,
    3 of 55 heads-up turn facing-a-bet decisions);
  - close river decisions facing a bet (`river-under-fold`, 1.55% of pot
    against 0.65%).

  A player who treats those two warnings as "slow down and consider the
  alternative the note names" avoids the most expensive advice measured
  here.

**In win-rate terms.** This is a rough bound, stated as one.

- **The rough sum:** on the turn and river in a 15 bb pot, following the
  advice costs up to about 0.1 bb per decision. At M183's ~1.4 postflop
  decisions a hand, that is on the order of **10–15 bb/100 at most** if
  every hand were a 3-bet pot, and about a third of that in single-raised
  pots, where the pot is a third the size.
- **For scale:** a solid online winning rate is roughly 5–10 bb/100, so
  the upper bound is the same order as the edge a player is trying to
  keep.
- **Why the true figure is lower:**
  - the bound is regret, so it overstates;
  - most of it lives in the flagged tail.
- **Why it could be higher:** the flop is not in it.
- **Not measured at all:** real opponents. Every number here is against
  a near-equilibrium opponent. Against a weaker one, a close-to-GTO line
  wins more, and a mistake at a close decision matters less.

**Where the read is weakest, in order:**

1. **Multiway postflop.**
   - **Disclosure:** 23% of all decisions carry
     `multiway_postflop_irreproducible`. A second solve of the same spot
     changes the top action 41–75% of the time (M245).
   - **Reference:** none. Treat it as a rough lean, not a read.
2. **The turn shove facing a bet.** Flagged; call instead.
3. **The flop facing a bet.** 74% agreement where the answer is clear,
   and no price.
4. **Preflop in a multiway pot that folds to two, with trash facing a
   4-bet** (M251). Flagged; it fires on 0.6–1.2% of decisions.

**Where it has NOT been checked from outside at all:**

- stacks other than 100 bb;
- single-raised turns and flops;
- 4-bet pots;
- any multiway spot.

### Speed — does it answer in time?

Measured in-process on this machine. There is no network and no
concurrent users, and the machine drifted 1.2–2.5× during the run.

| decision | median | 90th percentile | worst |
|---|---|---|---|
| preflop | **0.00 s** (cached) | 1.6–1.8 s | 4.9 s |
| flop, first to act | 1.6–2.0 s | 2.4–2.5 s | 4.5 s |
| flop, facing a bet | 1.5–1.7 s | 2.4–2.5 s | 2.8 s |
| turn, first to act | 1.1–1.3 s | 1.5 s | 3.2 s |
| river, first to act | 0.7–1.1 s | 3.1–3.4 s | 4.2 s |
| turn / river, facing a bet | 0.02 s* | 0.04–0.29 s | 0.9 s |
| **every decision** | **0.42–0.46 s** | **2.2–2.3 s** | **4.86 s** |

\* **Facing-a-bet times on the turn and river are mostly cache hits.**
In the benchmark those requests came right after the same street's
opening request, which pays for the solve. A player whose opponent acted
first has not made that request.

**Measured afterwards with every postflop cache cleared** (heads-up,
100 bb, 10 spots per street):

| street, facing a bet, cold | median | worst |
|---|---|---|
| flop | 2.80 s | 4.11 s |
| turn | 1.86 s | 2.51 s |
| river | 2.60 s | 3.85 s |

### The wait the benchmark could not see: multiway at an unprewarmed stack

The multiway preflop solve is cached per 5 bb stack bucket (M124), and
production prewarms only **100, 50 and 20 bb**
(`MULTIWAY_PREWARM_STACK_DEPTHS`). Both benchmark arms drew stacks from
exactly those three values. So every multiway request they made was
warm. **M252's population trap again: the benchmark measured the stacks
it generated.**

Measured on a fresh process:

| request | first time | again (same bucket) |
|---|---|---|
| heads-up preflop, 73 bb | 0.5 s | — |
| heads-up flop, 73 bb | 2.8 s | 0.0 s |
| **3-max preflop, 37 bb** | **51.9 s** | — |
| **6-max preflop, 73 bb** | **99.5 s** | 0.0 s |
| 9-max preflop, unprewarmed | **~525 s** by M157's cost (not re-run) | — |

- **Who waits:** a multiway player whose stack is not near 100/50/20 bb.
- **How often:** once per 5 bb bucket per table size, per server
  process. Every multiway postflop request needs this solve first, so
  those wait too.
- **Heads-up is unaffected:** the exact solver answers any depth in
  under a second.

**What that means at a table:**

- **At a prewarmed stack, everything returns inside 5 seconds.** 0 of
  2,293 decisions went over, 13–16% took more than 2 s, and 3.4–3.6%
  took more than 3 s.
- **At any other multiway stack, the first answer is too slow for any
  clock:** 52 s at 3-max, 99.5 s at 6-max, and minutes at 9-max.
- **Online:** a typical action clock is ~15 s before a time bank. A
  worst case under 5 s leaves room to read the answer and its notes.
- **Live:** there is no hard clock, and a 1–3 s pause is invisible.
- **The real time cost is the player's input,** not the solver. The
  action history has to be entered for every street, and on a multiway
  hand that is most of the time spent.
- **Not measured:**
  - many players sharing one server;
  - a server under load while a cold multiway depth is solving.

### Verdict

| use | ready? |
|---|---|
| heads-up turn and river decisions | **yes**, reading the warnings it attaches |
| heads-up flop: bet or check, call or fold | **mostly**; the size advice is unpriced |
| heads-up flop facing a bet | **with caution**; 1 clear decision in 4 disagrees |
| multiway postflop | **no** — a lean, not a read |
| multiway preflop, weak hands facing heavy action | **no** — flagged |
| speed, heads-up | **yes**; under 5 s at every depth, cold or warm |
| speed, multiway | **only at 100/50/20 bb**; elsewhere the first answer takes 52 s to minutes |

**The single most useful habit:** when a response carries `turn-shove`
or `river-under-fold`, take the alternative the note names seriously.
Those two warnings cover the most expensive advice this benchmark found.

**Today that habit is hard to act on.**

- **The front end never reads `advisory_notes`.** It shows every note
  joined into one paragraph.
- **On a heads-up turn facing a bet, that paragraph runs 3,236
  characters** — about 500 words, and it appears on every postflop
  decision. The `turn-shove` warning is its last sentence.
- **At the table,** a player on a 15 s clock will not get there (R11).

---

## Recommendations — amended after the live-play section

The usability review changed the priorities. Two findings the benchmark
could not see now lead, because they decide whether a player at a table
can use the product at all.

| # | recommendation | why (live play) | priority | status |
|---|---|---|---|---|
| **R11** | **Surface the warnings that matter.** Render `advisory_notes` by id, putting `turn-shove`, `river-under-fold`, `costly-band` first as short badges, plus the multiway irreproducibility reason from `solver_confidence_reason`, with the long standing caveat collapsed | The two warnings that avoid the most expensive advice are the last sentence of a ~500-word paragraph shown on every postflop decision | **1** | OPEN |
| **R12** | **Close the multiway cold-depth wait.** Warm every 5 bb bucket in the background after startup, most common stacks first. Meanwhile, answer from the nearest warmed bucket BELOW (F13's floor keeps sizes affordable), with a disclosure, rather than blocking. Draw benchmark stacks off the prewarm grid | First multiway answer at 73 bb took 99.5 s (6-max), 51.9 s (3-max), ~525 s at 9-max. Full warming costs ~1.6 CPU-hours at 3/6-max, and ~39 x 40 MB at 6-max, over the 1 GB cache budget, so the budget must move too; 9-max cannot be fully warmed at 257 MB an entry | **2** | OPEN |
| R14 | **Find where the flop facing a bet disagrees.** A frequency-only study, cheap: dumps need one round. Look for a runtime-readable gate, the way M243 found one for the river | The weakest heads-up cell in the usability table: 23 of 31 clear decisions agree. M188 puts the most postflop cost on this node, and it is unpriced | 3 | OPEN (supersedes R7's priority) |
| R13 | **Extend external coverage to the spots players meet:** stacks other than 100 bb, single-raised turns and flops, 4-bet pots | Every accuracy number above is heads-up, 100 bb, mostly 3-bet pots. Turn pricing is now cheap (R4); single-raised depth is the cost driver (M257) | 4 | OPEN |
| R7 | Price the flop's size disagreement | Size advice is the flop's biggest frequency gap, but a player can act on "bet or check" without it | 5 | OPEN |
| R15 | **Time the whole hand, not only the solve.** Measure how long a player takes to ENTER the action history per street in the front end | For a live player that is likely the larger delay, and it has never been measured | 6 | OPEN |
| R1–R6, R9, R10 | as above | — | — | DONE |
| R8 | Report river direction by pot type in any future river copy | unchanged | — | recorded |

**Retired as out of reach here:** measuring accuracy against real
(non-equilibrium) opponents. It needs play data this project does not
have. The win-rate bound in the usability section stays a bound against
a near-equilibrium opponent.

## CORRECTION (M277/A14): the turn grades are withdrawn

The turn references were re-solved from these same params and scored by
best response off their own dumps: **1.87-3.91% of pot**, against the
0.33-0.49% they report, and their own per-hand slack is **0.49-0.96%**.
The turn's grades here - 0.71% of pot opening, 1.20% facing - sit at or
barely above the instrument's own level, so they are not resolvable by
this reference and are withdrawn as grades.

**The turn-shove price (2.408 bb, 16% of pot) stands**: 4-8x that
level, and already published as a floor. River and flop figures are
unaffected (M276).
