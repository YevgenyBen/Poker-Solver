# M258 — the turn's price at depth, on the validated instrument

M257 built an instrument and proved it works: matched menus plus a
three-round dump reproduce the solver's own exploitability at **0.8×**
at SPR 6.17, below the one-sided bound theory requires. It then did not
use it, because the only price it measured (3.16% of pot) came from SPR
2.53 — the shallow regime, where M222 says the turn's disagreement is
*mildest*, while M199 puts **80% of real decisions at SPR ≥ 5**.

This spends the bill M257 priced: 425s and 3.47 GB a spot.

## The question, and it is a DIRECTION question

**Does the turn's price grow with depth?** Not "what does the shipped
turn cost" — that is out of reach and the reason is structural, stated
here rather than discovered later:

**The instrument can have depth or width, not both.** These run at cap
12; the turn ships at **cap 140**, and M232 measured the turn's
frequency gap *doubling* between cap 25 and 140. A three-round dump at
production width extrapolates to tens of GB against a 3.47 GB dump that
already strains a 31.7 GB machine (F67). So nothing here is the shipped
product's cost, and **no constant in `api/config.py` will move on it.**

## The reading rule — fixed before the run

Against M257's SPR 2.53 figure of **3.16% of pot** (turn opening, mean
regret 1.0412 bb):

| turn regret at SPR 6.17 | reading |
|---|---|
| materially larger, 2 sigma | **3.16% is a FLOOR.** The shallow regime understates, as M222's SPR-dependence predicts, and every prior figure taken shallow is a lower bound |
| comparable | the turn's price is **roughly depth-independent**, and the shallow figure travels — which would make it far more useful than it looks |
| materially smaller | the shallow regime **overstates**, and 3.16% is the wrong end of the range |

**Controls, all inherited from M257 and none relaxed:**

- the **flop opening** cell is the control. It must price at ~0. If it
  does not, nothing is concluded — the instrument is wrong, whatever the
  turn says;
- **`bench.dump_control` gates per (spot, cell)**, not per study, and a
  cell whose reference slack approaches its regret is reported
  unresolvable rather than as a small number;
- **remapped mass** is reported per row. It should be 0.0000 on matched
  menus; anything else means the menu drifted and the row is void;
- the **zero control** must stay exactly 0.0.

**Expected instrument behaviour, written down so a miss is visible**:
the control ratio should land **below 1.0**, as it did at 0.8× in M257.
A spot coming back above 1.0 is a spot whose dump is not doing what this
milestone assumes.

## Cost, measured rather than assumed

**A flop row costs ~600 seconds on a three-round dump.** It walks the
whole tree — 49 turn cards, each with 47 rivers — and five of them took
fifty minutes. At that rate one spot is over three hours and this study
is twenty.

**A turn row is ~49× cheaper**, because it starts AT a turn node and
walks one river. So the expensive cells are exactly the ones M258 is not
asking about, and the study was rescoped rather than run at a rate that
would not finish:

| | |
|---|---|
| flop opening (control) | **3 rows** — enough to see whether it prices at ~0 |
| turn opening | **8 rows × 2 turn cards** |
| flop facing a bet | **dropped** — a flop cell at ~600s, and not the question |

**Rows are now checkpointed individually.** The first attempt lost fifty
minutes of completed rows because a spot was only saved once it
finished, and a spot here takes hours.

**And a third cost, not in F55's model at all**: the resident dump
measured **15.3 GB** of working set for a 3.36 GB file. That is the
reading constraint F67 named, quantified — roughly 4.5× the file, which
is what caps this at one spot in memory at a time.

## FINAL RESULT — on the corrected metric

Everything below this section was measured with an unbounded `max_a` and
is superseded; it is kept because the corrections are the substance of
this milestone. Six spots, SPR 6.17, cap 12, three-round dumps, matched
menus, regret bounded to the reference's own support, both arms on one
support, gated per (spot, cell).

**The gate passes 11 of 12 cells.** Only `Th9c8d`'s turn fails, at 63×.

| cell | n | mean regret | % of pot | median | sigma | zero control |
|---|---|---|---|---|---|---|
| flop opening | 23 | 0.0565 | **0.38%** | 0.0007 | 2.51 | 0.0 |
| turn opening | 90 | 0.0824 | **0.55%** | 0.0169 | 6.24 | 0.0 |

### Only ONE of those two numbers is usable, and the other is the finding

**Off-support mass — the share of OUR row on actions the reference never
plays — decides how much of our strategy the figure covers:**

| cell | mean | median | rows > 0.5 | rows > 0.9 |
|---|---|---|---|---|
| flop opening | **0.351** | 0.251 | 6 of 23 | **4** |
| turn opening | **0.109** | 0.016 | 3 of 106 | 0 |

**The turn figure is real.** It covers ~89% of our row, no row exceeds
0.9, and the gate passes on 5 of 6 spots: **our turn advice at SPR 6.17
sits 0.55% of pot from the best action the reference actually plays.**

**The flop figure is not.** A third of our flop strategy is on actions
the reference never takes, and per spot it runs:

| spot | flop off-support |
|---|---|
| `Kd7c2h` | **0.988** |
| `9d9s4c` | 0.447 |
| `8h6h2s` | 0.436 |
| `5c4d2h` / `Ac7d2h` / `Th9c8d` | 0.086–0.087 |

On `Kd7c2h` **98.8% of our flop strategy is on actions the reference
never plays** — it checks that flop 100% and we bet it almost always —
so "regret 0.0000" there is computed on the 1.2% that overlaps and means
the opposite of agreement.

**So at a pure or near-pure reference node, regret cannot express
disagreement at all**, and the only meaningful measure is the frequency
question: does our row play what the reference plays? That is the
frequency metric this project moved away from in M182/M183, arriving
back by necessity rather than preference.

### The depth comparison is STILL not available

M257's 3.16% at SPR 2.53 was computed with the **unbounded** metric,
which this milestone showed inflates figures by maximising over
untrained subtrees. **0.55% against 3.16% is not a comparison** — the
two use different instruments, and the shallow figure would shrink too
if recomputed. The pre-registered "materially smaller → the shallow
regime overstates" reading is therefore **not** claimed.

What M258 delivers instead is a single clean number on a validated
instrument, and the knowledge of exactly which cells it can speak for.

---

## Result — INCONCLUSIVE, because the control fails

### The verdict, over all six spots

**The gate passes 11 of 12 (spot, cell) pairs.** Turn cells pass at
ratios 0.01, 0.07, 0.21, 1.29 and 1.62; only `Th9c8d` fails, at 63×.

| cell | n | mean regret | % of pot | sigma | remapped | zero control |
|---|---|---|---|---|---|---|
| **flop opening** (control) | 23 | 0.386 | **2.57%** | **4.15** | 0.0000 | 0.0 |
| turn opening | 90 | 0.1597 | **1.06%** | 8.53 | 0.0000 | 0.0 |

**The control fails.** It must price at ~0; it prices at 2.57% of pot,
separable at 4.15 sigma — and it prices HIGHER than the turn, which
contradicts the six external studies putting the flop as this engine's
strongest street. By the rule fixed before the run, **nothing is
concluded.**

**What the turn figure would have said, had the control been clean**:
1.06% of pot at SPR 6.17 against M257's 3.16% at SPR 2.53 — the THIRD
pre-registered reading, that the shallow regime overstates. It is
recorded here and **not claimed**, because a control exists precisely to
stop a tidy number being published when the instrument says no.

### The one spot that failed, and my over-reading of it

#### `Th9c8d` in detail

First spot, `Th9c8d_three_bet_c12` (the connected T-9-8 board, the one
that failed M257's two-round control at 50×), on a 3.36 GB three-round
dump:

| cell | n | regret | % of pot | ref slack | ratio | remapped |
|---|---|---|---|---|---|---|
| **flop opening** (control) | 4 | 0.1325 | 0.88% | **0.0000** | **0.00** | 0.0000 |
| turn opening | 16 | 3.3970 | 22.65% | **3.1969** | **63×** | 0.0000 |

**The control passes completely** — the reference's own slack at the
flop root is *exactly* zero and our flop regret is 0.88% of pot. The
instrument is sound on this dump, and remapped mass is 0.0000, so the
menus match as F66 intended.

**The turn cell is refused by its gate**, and the shape of the failure
names the cause. Regret (3.184) and slack (3.197) are nearly equal,
which means **our row and the reference's row agree with each other**
and both sit ~3.2 bb below whatever the walk calls best. That is not two
strategies disagreeing; it is one action looking inflated.

**The inflated action is a 6×-pot overbet shove.** `BET 92` into a 15 bb
pot is the walk's "best action" on 12 of 16 turn rows. A converged
solver does not leave 4.86 bb on the table by declining to shove pocket
fives — villain would call wider. What it means is that **villain's
response to a rare overbet is the least-trained branch in the tree**, so
in the dump villain over-folds there and the shove reads as free.

#### The mechanism, and its real scope

F58 replaced `EV(reference) − EV(ours)` with `max_a Q(a) − EV(ours)` and
justified it as non-negative by construction. It is. **It is not
unbiased.** `max_a` deliberately searches for the action with the
highest value, and in a reference solved by reach-weighted effort that
search lands on **whichever subtree the solver trained least**. The
metric therefore inherits an upward bias that grows with how rare the
node's best-looking action is.

The flop root is high-reach and converged — slack 0.0000. A turn node's
overbet is neither. **So the bias is not uniform and cannot be
subtracted; it is exactly the reason the per-(spot, cell) gate exists.**

**What a reference for this needs is now specific**: not a lower average
exploitability (0.337% was already converged to target), but convergence
**at the node and action level** — every branch a best response might
choose, trained enough to be worth maximising over. A reach-weighted
solve does not provide that and will not provide it by being asked
harder in aggregate.

**Consequence for M257's headline.** The 3.16%-of-pot turn figure at SPR
2.53 was measured with the same metric and is subject to the same bias.
Its cells passed their gate, which bounds the problem, and the gate is
now the only thing separating a usable figure from an artifact.

**M258's answer to its own question is: the turn's price at depth is NOT
measured.** The control failed, so the rule refuses the result — and the
figure it refuses (1.06%, lower than shallow) is one a motivated reading
would have been happy to publish.

### CORRECTION — the control failure was the METRIC, not our advice and not the rule

The section below concluded that our flop advice is genuinely worse at
depth and that the control rule had imported a shallow-regime
assumption. **Both are withdrawn.** Bounding regret to the actions the
reference actually plays (`MIN_ACTION_SUPPORT`) moves the flop cell:

| flop opening | mean regret | % of pot |
|---|---|---|
| unrestricted max | 0.386 bb | 2.57% |
| **bounded to the reference's support** | **0.0518 bb** | **0.35%** |

**Why is visible in one line.** Action support at that flop root:

    CHECK 1.0000   BET 5 0.0000   BET 11 0.0000   BET 38 0.0000   BET 92 0.0000

**The reference never bets this flop.** Every bet subtree is completely
untrained, and an unrestricted `max_a` was maximising over noise. The
control rule — "the flop must price at ~0" — was right; the instrument
feeding it was not. The M168 self-criticism attached to it is withdrawn
with the claim.

**The cross-cell confound stands, and this sharpens it**: the bias is
not about subtree size as such, it is about **how much untrained subtree
a cell's `max_a` can reach**, and a flop node whose every bet is untrained
is the extreme case. M257's "facing a bet is 38× the opening decision"
remains unproven for the same reason.

**The turn is now the marginal case.** Support at the turn node:

    CHECK 0.7354   BET 5 0.1368   BET 11 0.0661   BET 38 0.0506   BET 92 0.0111

The overbet shove clears the 0.01 floor **by a hair**, which is why the
turn figure did not move at all when the flop's fell fivefold. A result
that turns on where a hand-picked line falls relative to one action is
not a result, so the threshold is swept rather than defended.

### The control rule was WRONG, not the data — and the cells are not comparable

The flop cell is not a starved sample. Removing its top two rows still
leaves **1.89% of pot**, and the per-spot values are 0.2–0.5 bb
consistently across all six boards. It is a real measurement.

**So the control rule was the error, and it was mine.** "The flop must
price at ~0" was pre-registered on M221 and M242 — measured at shallow
depth — while **M195/M196 had already measured the flop's street
isolation bias GROWING with SPR** (+0.0981 at SPR 7.5, +0.2183 at SPR
16.2). I imported a shallow-regime property into a deep-regime control.
That is M168's rule, broken by the person who keeps citing it.

**And `bench.dump_control` — which tests the instrument rather than my
assumption — PASSED every flop cell** (ratios 0.00–1.72). The instrument
is sound; 2.57% is a real measurement of our flop regret at SPR 6.17.

**But the two cells cannot be compared, and that is the finding.**
Regret is `max_a Q(a) − EV(ours)`, and `max_a` searches for the most
valuable action — which in a reach-weighted reference means **searching
for the least-trained subtree**. A flop node has the whole turn and
river below it: **49 turn cards × 47 rivers** of branches to search. A
turn node has one street. **So the bias grows with subtree size, and the
flop cell is the larger search by three orders of magnitude.**

Flop 2.57% against turn 1.06% is therefore **not** evidence that the
flop is the worse street. It is consistent with the flop simply
offering `max_a` more places to find an under-trained branch. The two
numbers are not on the same scale.

**This reaches back into M257.** Its "facing a bet is 38× the opening
decision" compares cells whose subtrees differ the same way, and is
subject to the same confound. It was reported as reproducing M188/M189's
20–27× from an independent instrument; **that agreement may be
coincidence**, and the claim should be treated as unproven rather than
as replication.

**What regret IS good for**: comparing the same cell across arms —
configurations, depths, widths — where the subtree is the same shape and
the bias is common to both. That is how F62, F66 and F67 used it, and
those results stand.

### What actually needs explaining now

**Why does the flop control price at 2.57% when the turn prices at
1.06%?** Six external studies put the flop as this engine's strongest
street, and M257's own shallow run had the flop control at 0.24% and not
separable from zero. Candidates, none tested:

- **the flop cell is n=23 at 3 rows a spot**, chosen by a stride over
  the range — a thin and possibly unrepresentative sample, where the
  turn has 90;
- the same overbet-exploitation that failed `Th9c8d`'s turn may be
  present and sub-threshold in flop cells that passed;
- something about a flop row on a three-round dump that a turn row does
  not meet.

**The cheapest discriminator is the first**: re-run the flop cell at the
turn's row count on one spot. If 2.57% collapses toward M257's 0.24%,
the control was starved rather than wrong, and this study can be re-read
with a proper control. That is the next step, and it is minutes of
solving plus an hour of walking.
