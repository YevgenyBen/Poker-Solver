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
