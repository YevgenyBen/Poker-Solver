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
| **Turn, opening** | **B** (0.68%) | n=5; frequency D (0.298) |
| **Turn, facing a bet** | **D** (2.19%, ±0.80) | n=17; the most expensive cell measured; upper bound |
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

### E2 — the turn facing a bet is the most expensive cell measured

- **The price:** 2.19% of pot ±0.80 over 17 rows; 1.03% net of the
  reference's own slack. The worst row is 10.6%.
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
| R4 | Make the turn walk cheaper, then price the turn at n ≥ 24 | OPEN — the walk is 30 min a spot at cap 140; the solve is 15 s |
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
