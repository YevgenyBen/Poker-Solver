# Recommendations — 2026-09-17

Sources: the wide benchmark (`docs/wide-benchmark-2026-09-16.md`, M260)
and the Pluribus check (`docs/pluribus-multiway-2026-09-17.md`, M262).
Ordered by what they change for a player at a table, then by cost.

| # | recommendation | from | why | status |
|---|---|---|---|---|
| **A1** | **Never answer 200 without a hero row.** Treat a missing hero strategy as low confidence with a named reason, and make every benchmark harness flag it as a defect | M262 | A player got `strategy: null` at 200 and no benchmark noticed. The cause is fixed; the silence is not | **DONE** — `MISSING_HERO_ROW_REASON`; `bench.advise_checks.response_defects` for every harness |
| **A2** | **Surface the warnings that matter** in the front end: `advisory_notes` rendered by id as short badges, with `multiway-bet`, `turn-shove`, `river-under-fold` and `costly-band` first, and the long standing caveat collapsed | M260 R11 | The most useful warnings sit at the end of a ~500-word paragraph | **DONE** — `advisory_note_details` in the API; `DecisionWarnings` in the front end; checked in the browser |
| **A3** | **Close the multiway cold-depth wait.** Warm buckets in the background after startup, in the order real stacks occur (from the hand store), and serve the nearest warmed bucket below with a disclosure until the real one is ready | M260 R12 | First answer at 73 bb 6-max took 99.5 s; 9-max ~525 s | **DONE (warming only)** — the nearest-bucket fallback was measured and refused (the depth control exceeds seed noise at every gap); `MULTIWAY_BACKGROUND_WARM` warms 22 six-max and 37 three-max buckets while idle; live, a 107 bb 6-max request went ~100 s → 0.24 s; `GET /warm_status` |
| **A4** | **Use Pluribus as the yardstick multiway never had,** and find which lever fixes the over-betting: node-training reach, range cap 8, iterations, ensembles | M262 | Multiway postflop is −0.184 against a card-blind prior. Every multiway configuration was refused before because nothing could score it; now something can | OPEN |
| **A5** | **Replay real online hands through `/advise`** as a standing defect benchmark: real stacks, real sizes, real table sizes | M262, M260 R13/R15 | Replaying real hands found a defect no synthetic population reached | OPEN |
| A6 | Find where the flop facing a bet disagrees (a frequency study, cheap) | M260 R14 | 23 of 31 clear decisions agree; unpriced | OPEN |
| A7 | Extend external coverage: other stacks, single-raised turns and flops, 4-bet pots | M260 R13 | Every external figure is 100 bb and mostly 3-bet | OPEN |
| A8 | Price the flop's size disagreement | M260 R7 | Needs three-round dumps or a streaming reader | OPEN |

Implementation proceeds A1 → A5, one merged change each. A6–A8 are
queued.
