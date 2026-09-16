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

*(Results follow.)*
