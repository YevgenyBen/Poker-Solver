# M259 — what the river under-fold costs, priced from outside

M243 found that when this engine's river row is **mixed** (top action
under 0.80), it folds far less than an independent reference facing the
same bet: **−0.3295 at 5.96 sigma**, reference 0.7078 against ours
0.3783, on 21% of river facing-a-bet decisions. The note the player
reads says the cost is **unknown**, because M244 tried to price it
inside our own model and a model cannot referee a disagreement about
itself (−0.9644 bb, corr −0.745).

This prices it inside the reference's game instead.

## Why this cell can be priced when the turn could not

Every failure M257 and M258 met is absent or at its smallest here:

| M257/M258 failure | on the river |
|---|---|
| leaf approximation (F62) | **absent** — the board is complete |
| untrained-subtree bias in `max_a` (M258) | **smallest** — fold and call lead straight to showdown |
| width compromise (cap 12 vs 140) | **absent** — references run at cap 60, the width the river SHIPS at |
| reference cost | **negligible** — one street, 0.49 MB a dump |
| menu mismatch (F64) | **absent** — references carry 33/75/250 plus all-in |
| force-included heroes (M243) | **absent** — heroes drawn from ranges derived with `hero_combo=None` |

**No new solves.** M243 left 50 in-range references on disk, 10 per hand
type, each with its exploitability recorded (0.108–0.497%).

## The comparison, and why it is a sound one

The shipped gate (`RIVER_UNDER_FOLD_MIXED_MAX_TOP_ACTION = 0.80`) splits
the 50 spots by **our own** row, read live from `/advise`:

- **firing** — top action under 0.80, where the note speaks;
- **silent** — top action at or above 0.80, where it does not.

**Both are the same cell**: the river, facing the same 4.95 bb bet, on
the same tree shape. M258 established regret is sound for the SAME cell
across arms and unsound across cells of different sizes; this is the
former. Whatever bias `max_a` carries lands on both sides.

## The reading rule — fixed before any number exists

| firing regret vs silent regret | reading |
|---|---|
| firing materially larger, 2 sigma | **the note's gap costs money**, and the firing-cell mean is the figure the note should quote |
| comparable | **the gap is cheap** — the note should say so, with the number |
| firing smaller | the gate points at the WRONG decisions, and the note needs rethinking rather than a number |

**Controls, none of them relaxable:**

- **silent rows are the control.** M243 measured them at −0.0250 (1.10
  sigma) in frequency; they must price near zero regret or the
  instrument is wrong;
- **`bench.dump_control` gates per spot** against its own recorded
  exploitability;
- **off-support mass and remapped mass are reported on every row**, and
  a cell whose mean remapped mass exceeds 0.5 is reported as
  mapping-dependent rather than as a cost. Our re-raise (2.0× the bet)
  and the reference's (60% of pot) differ, so this is expected to be
  nonzero on the raise action;
- **rows where the reference is pure are flagged**, because regret
  cannot express disagreement there (M258);
- **`MIN_ACTION_SUPPORT` is swept** (0.00 → 0.20) before any figure is
  quoted, because M258 measured a 43× dependence on it.

**Scope, stated now**: 50 spots, one line (three-bet pot, flop and turn
checked, SPR 6.17), one bet size (the smallest). Nothing here licenses a
claim about other lines or sizes (M168).

## First pass — the instrument is sound, the cell is mixed

All 50 spots pass the root control (reach 1), and the sweep reproduces
`regret_of_row` on 50 of 50 rows.

| threshold | firing (n=9) | silent (n=41) | difference | sigma |
|---|---|---|---|---|
| 0.000 | 0.240 bb (1.60%) | 0.101 (0.67%) | +0.140 | 2.03 |
| **0.010 (shipped)** | **0.231 (1.54%)** | **0.100 (0.67%)** | **+0.130** | **1.80** |
| 0.020 | 0.231 | 0.078 | +0.153 | 2.21 |
| 0.050 | 0.211 | 0.073 | +0.138 | 1.93 |
| 0.100 | 0.119 | 0.060 | +0.059 | 0.79 |

**Unlike M258 there IS a plateau** across 0.00–0.05; it breaks at 0.10,
where a quarter of the firing row is off-support.

**Not a verdict, for two reasons found on reading the rows:**

1. **n=9 and 1.80 sigma at the shipped threshold** — short of the 2
   sigma the rule requires.
2. **The firing gate mixes two different disagreements.** Only **3 of 9**
   are under-folds (we fold 0.28 / 0.40 / 0.25 where the reference folds
   0.44 / 0.69 / 0.62). **Five are strong hands that never fold**, where
   the disagreement is RAISE SIZE — and three carry remapped mass
   0.53–0.72.

**The cause is the reference's RE-RAISE menu at this node** — M242's
warning ("match the menu at the FACING node") one level below F64. With
`raise_pct = 60` its re-raise lands at `RAISE 20`; ours is 2.0× the bet
faced, 9.90. The reference's re-raise is
`5 + pct × (15 + 5 + 5)`, so **`raise_pct = 20` lands at 10**, 1% from
ours (M241 derived the same).

The cell's MEAN remapped mass (0.255) is under the pre-registered 0.5,
so by the rule's letter it is not mapping-dependent — but three rows
individually are, and at **4.5 seconds a reference** fixing it is nearly
free.

## Second pass — the design change, fixed before it runs

- **Every reference re-solved at `raise_pct = 20`**, everything else
  unchanged. Remapped mass on the strong-hand firing rows should
  collapse; if it does not, the size is not the cause.
- **The firing sample extended to at least 25** by screening fresh
  spots through `/advise` and solving references only for those that
  fire (heroes still drawn from ranges derived without force-inclusion).
- **The under-fold claim is judged on the rows where folding is in play**
  (either arm folds at least 5%), because that is what the note is
  about. Firing rows where neither arm folds are reported separately as
  a sizing disagreement, not folded into the note's figure.
- **The reading rule is unchanged**: firing materially above silent at 2
  sigma means the gap costs money and the firing-cell mean is the figure;
  comparable means cheap; smaller means the gate points at the wrong
  decisions.

## Result

*(Pending.)*
