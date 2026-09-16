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

## Result

*(Pending.)*
