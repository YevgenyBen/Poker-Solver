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

## Result

*(Pending.)*
