# Recommendations — 2026-09-17

Sources: the wide benchmark (`docs/wide-benchmark-2026-09-16.md`, M260)
and the Pluribus check (`docs/pluribus-multiway-2026-09-17.md`, M262).
Ordered by what they change for a player at a table, then by cost.

| # | recommendation | from | why | status |
|---|---|---|---|---|
| **A1** | **Never answer 200 without a hero row.** Treat a missing hero strategy as low confidence with a named reason, and make every benchmark harness flag it as a defect | M262 | A player got `strategy: null` at 200 and no benchmark noticed. The cause is fixed; the silence is not | **DONE** — `MISSING_HERO_ROW_REASON`; `bench.advise_checks.response_defects` for every harness |
| **A2** | **Surface the warnings that matter** in the front end: `advisory_notes` rendered by id as short badges, with `multiway-bet`, `turn-shove`, `river-under-fold` and `costly-band` first, and the long standing caveat collapsed | M260 R11 | The most useful warnings sit at the end of a ~500-word paragraph | **DONE** — `advisory_note_details` in the API; `DecisionWarnings` in the front end; checked in the browser |
| **A3** | **Close the multiway cold-depth wait.** Warm buckets in the background after startup, in the order real stacks occur (from the hand store), and serve the nearest warmed bucket below with a disclosure until the real one is ready | M260 R12 | First answer at 73 bb 6-max took 99.5 s; 9-max ~525 s | **DONE (warming only)** — the nearest-bucket fallback was measured and refused (the depth control exceeds seed noise at every gap); `MULTIWAY_BACKGROUND_WARM` warms 22 six-max and 37 three-max buckets while idle; live, a 107 bb 6-max request went ~100 s → 0.24 s; `GET /warm_status` |
| **A4** | **Use Pluribus as the yardstick multiway never had,** and find which lever fixes the over-betting: node-training reach, range cap 8, iterations, ensembles | M262 | Multiway postflop is −0.184 against a card-blind prior. Every multiway configuration was refused before because nothing could score it; now something can | **DONE** — iterations ×4 adopted (+0.107, 7.86σ); cap and ensemble inert; one-size diagnostic names action-count dilution (M264) |
| **A4b** | **Give the solver a starting strategy that does not weight betting by how many bet sizes exist** | M264 | With one sized bet, multiway advice reaches the card-blind prior | **DONE — NULL** (M265: −0.005, −0.49σ; not adopted; mechanism withdrawn) |
| A4d | Test whether regret matching over-weights a group of near-duplicate bet sizes (group-level matching, or size menus solved as one action then split) | M265 | The one-size result stands and its mechanism is unknown | OPEN |
| A4c | Re-measure multiway reproducibility (M245/M254) at the new budget | M264 | The quoted instability was measured at 1000 iterations | **DONE** (M267) — action changes 28% → 21%, all of it the river (0.53 → 0.31, TVD −0.134 at 3.06σ); decisive river rows now as stable as the turn's, so the river exclusion is removed (gate still 6.23σ); copy re-quoted |
| **A5** | **Replay real online hands through `/advise`** as a standing defect benchmark: real stacks, real sizes, real table sizes | M262, M260 R13/R15 | Replaying real hands found a defect no synthetic population reached | **DONE** (M266) — `bench/real_replay.py`; 1,199 of 1,200 answered, 0 defects; fixed an all-in preflop line refused as "stack_bb must be positive"; 4+ live pots keep the pre-M264 budget (x4 cost 3.4x there for +0.056 at 1.01σ) |
| A6 | Find where the flop facing a bet disagrees (a frequency study, cheap) | M260 R14 | 23 of 31 clear decisions agree; unpriced | OPEN |
| A7 | Extend external coverage: other stacks, single-raised turns and flops, 4-bet pots | M260 R13 | Every external figure is 100 bb and mostly 3-bet | OPEN |
| A8 | Price the flop's size disagreement | M260 R7 | Needs three-round dumps or a streaming reader | OPEN |

Implementation proceeds A1 → A5, one merged change each. A6–A8 are
queued.

## Added while implementing

| # | recommendation | from | why | status |
|---|---|---|---|---|
| **A9** | **Support 4-, 5-, 7- and 8-handed tables** (and 10 where a site deals it) | A5 scoping, hand store | Only 2/3/6/9 players are representable: **43.6% of real hands** have another player count. 5-handed alone is 19% and 4-handed 9%. With stacks over 200 bb also excluded, only **46%** of real hands can be asked about at all | OPEN |
| A10 | Measure whether the x4 multiway budget pays at 4+ live players, with a sample big enough to answer (Pluribus has 38 such decisions) | M266 | Reverted on cost, not on evidence of no benefit | OPEN |
