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

**Rule reading one: the note's gap costs money.** 75 matched-raise
spots. All 75 pass the root control and all 75 agree with
`regret_of_row`. Exploitability runs 0.199-0.499% with zero refusals.
**Remapped mass is 0.0000 on every row**, so matching the re-raise
removed the first pass's mapping problem.

| threshold | firing (n=34) | silent (n=41) | difference | sigma |
|---|---|---|---|---|
| 0.000 | 0.2692 bb (1.79%) | 0.0659 (0.44%) | +0.2032 | 4.08 |
| **0.010 (shipped)** | **0.2684 (1.79%)** | **0.0657 (0.44%)** | **+0.2026** | **4.07** |
| 0.020 | 0.2649 | 0.0655 | +0.1994 | 3.97 |
| 0.050 | 0.2463 | 0.0651 | +0.1812 | 3.67 |
| 0.100 | 0.1886 | 0.2702 | -0.0816 | -0.36 |
| 0.200 | 0.6752 (32% off-support) | 0.6902 | -0.0150 | -0.03 |

**The figure is flat from 0.00 to 0.05 and breaks at 0.10.** From 0.10
up, dropping actions the reference plays 5-10% of the time starts to
carry the answer (M258). So the firing figure is an **upper bound**,
and it is quoted as one.

**Checks against over-reading it:**

- **Medians:** firing 0.1596, silent 0.0118. Rows over 0.5 bb: 4
  firing, 0 silent.
- **Split-half on the firing rows:** +0.1616 (2.82 sigma) and +0.2436
  (3.19 sigma).
- **By origin:** the 25 screened spots average 0.2759 and the 9 M243
  spots 0.2476. M243's population alone gives firing against silent
  +0.1819 at 1.90 sigma. The size matches; the sample is 9 spots.
- **Reference slack:** the reference's own row carries 0.1055 bb of
  regret on firing rows and 0.0016 on silent ones. The reference is
  least exact exactly where it mixes. **Net of its own slack the
  difference is +0.1224 at 2.52 sigma.** That still clears the rule, and
  the copy says part of the figure is the reference's own imprecision.

### It is not only about folding

The split pre-registered for the second pass, where either arm folds at
least 5%:

| group | firing | silent | difference | fold, ours vs ref |
|---|---|---|---|---|
| fold in play | n=18, 0.2613 | n=13, 0.0985 | +0.1628 (2.17 sigma) | **0.306 vs 0.592, -0.2867 at 4.07 sigma, 14 of 18** |
| never folds | n=16, 0.2763 | n=28, 0.0505 | +0.2258 (3.17 sigma) | 0.001 vs 0.002 |

- **The under-fold replicates.** It holds on matched raises and on fresh
  spots selected only by our own row. It is smaller than M243's
  0.7078 / 0.3783, in the same direction, at the same 4 sigma.
- **The cost is not confined to the under-fold.** Firing rows where
  neither arm folds cost just as much. The gate finds expensive close
  decisions, and folding is one way that shows, not the only one.

### What changed in the product

`RIVER_UNDER_FOLD_NOTE` now quotes a price.

- **The price:** up to **0.27 bb** (about 2% of pot) where it fires,
  against **0.07** where it is silent. The copy calls this an upper
  bound and says part of it is the reference's imprecision.
- **The frequency figures** move to this population: 18 spots, 59%
  against 31%, under-folding on 14 of 18.
- **Two separate claims:** the copy now says "fold more here" apart
  from "this whole mix is costly".

**M244's argument is withdrawn.** It held that the actions being mixed
are closer in value, so the disagreement was more likely cheap. The
close decisions cost about **four times** what decisive ones do. The
gate and its threshold are unchanged.

### Two things noted and not acted on

- **The quoted M243 frequencies do not reproduce on the clean
  population.** They came from 16 firing rows at `raise_pct` 60, with
  force-inclusion effects already stripped. Here, the first pass's
  firing rows gave ours 0.10 against the reference's 0.19. The copy
  now quotes this study's figures, which come from a matched menu and a
  larger sample.
- **`classify` in M243's harness ranks hands absolutely.** A hand whose
  board plays is labelled `nutted` or `two_pair_plus`. This study used
  the fold-in-play split instead of hand class, so nothing depends on
  those labels.
