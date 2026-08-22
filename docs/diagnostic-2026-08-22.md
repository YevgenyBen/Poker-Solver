# Full diagnostic: code, performance, and live-play simulation (2026-08-22)

A whole-product diagnostic run after M66-M75 — the convergence and
equity work. Unlike `project-audit-2026-08-21.md`, which scanned
structure and benchmarked endpoints, this one adds the question the
product actually exists to answer: **a player is sitting at a table
holding two cards. Do we tell them what to do?**

Method: 17 live scenarios driven through `POST /advise` at **production
settings** — no shrunken pools, no monkeypatched caps — spanning
heads-up / 6-max / 9-max, every street, and 100bb / 30bb / 20bb stacks.
Plus a code scan and a frontend health check.

**Headline: the engine is in good shape and the product is not.** The
solver work of the last ten milestones landed — heads-up is fast and
correct end to end, and 3-max/6-max preflop is sound. But a **cache-key
defect means that on a shared server only the first player to ask about
a given spot gets advice at all**, and 9-max returns confidently wrong
advice. Neither is visible from the test suite.

---

## 1. What was measured

| | |
|---|---|
| Live scenarios | 17, through `/advise`, production settings |
| Backend tests | 766 passing |
| Frontend | 145 tests passing, `tsc --noEmit` clean, `oxlint` clean |
| Engine source | 6,555 lines across 16 modules |
| API source | 4,405 lines across 5 modules |
| Test source | 10,816 lines |
| Dead public engine functions | **0** (scanned every `def` not prefixed `_`) |

---

## 2. Live-play simulation — the product's own question

`ANSWER` = did we return a strategy at all. `TRAINED` = a real solve
rather than the uniform placeholder. `SANE` = agrees with facts true
regardless of solver internals (AA never folds; 72o folds a lot from
early position; a set continues).

| Scenario | Answer | Trained | Sane | Time | Top action |
|---|---|---|---|---|---|
| HU preflop, AA | yes | yes | ok | 3.4s | call |
| HU preflop, 72o | yes | yes | ok | 0.0s | fold |
| HU preflop, T9s | yes | yes | – | 0.0s | raise 2.5 |
| HU flop, top set | yes | **null** | ok | 8.7s | call |
| **HU flop, AK overcards** | **NO** | – | – | 0.0s | **none** |
| HU turn, set | yes | yes | – | 20.3s | call |
| HU river, set | yes | yes | – | 15.8s | all-in |
| 6max preflop UTG, AA | yes | yes | ok | **93.0s** | raise 2.5 |
| 6max preflop UTG, T7s | yes | yes | ok | 0.0s | fold |
| 6max facing a raise, QQ | yes | yes | ok | 0.0s | call |
| 6max flop, 3 live, set | yes | yes | – | 21.3s | call |
| 6max turn, 3 live, set | yes | yes | – | 4.5s | raise 22.5 |
| 6max river, 3 live, set | yes | yes | – | 3.5s | call |
| **9max preflop UTG, AA** | yes | yes | ok\* | **126.4s** | **all-in** |
| **9max preflop UTG, T7s** | yes | yes | **FAIL** | 0.0s | **call** |
| HU preflop 20bb, AA | yes | yes | ok | 1.5s | raise 2.5 |
| 6max preflop 30bb, KK | yes | yes | ok | **66.2s** | raise 2.5 |

\* passed only because the check was "AA does not fold". Shoving 100bb
with AA under the gun is not correct play.

---

## 3. Findings, ranked

### F1 — Advice depends on what someone else asked first (CORRECTNESS, severe)

`hero_cards` is force-included into every live position's derived range
before the top-K cap, so hero's hand is guaranteed to be solved for.
**But no cache key anywhere includes hero.** Verified by reading every
`key = (...)` construction in `api/solving.py`: keys carry action path,
stack, iterations, board, players — never hero.

So the first request for a spot fixes the solved pool, and every later
request for that same spot with a **different hand gets no advice at
all**. Measured, both directions:

```
ask AsKd then 9s9d:   AsKd -> advice     9s9d -> NONE
ask 9s9d then AsKd:   9s9d -> advice     AsKd -> NONE
cache cleared each:   AsKd, 9s9d, AsAh, KsQs -> all give advice
```

This is what produced the one hard failure in §2. It is invisible to the
test suite because tests clear caches between cases — precisely the
condition under which the bug cannot appear. On a server handling more
than one hand, most users get silence.

**Severity: this alone makes the product non-viable as a multi-user
service.**

### F2 — 9-max gives confidently wrong advice (CORRECTNESS)

T7s under the gun at a 9-handed table returns **call** as its top action;
correct play folds it near 100% of the time. AA returns **all-in** for
100bb. Both are marked `trained: true`, so nothing signals the problem.

Root cause is known and measured (M70-M72): iterations divide among
seats, so 9-max's 3,000-iteration budget gives each of nine positions
only 333 traversals against 6-max's 500 at the same budget. It is
under-trained, not structurally broken — but a wrong answer presented
confidently is worse than a refusal.

### F3 — Cold-start latency makes live use impossible off the warm path (PRODUCT)

Multiway preflop cold solves: **6-max 93s, 9-max 126s, 6-max at 30bb
66s.** Only `stack_bb=100` is pre-warmed, and only for the three table
sizes. A player at a 30bb table waits over a minute for their first
answer, with a clock running. Heads-up is fine (3.4s cold, instant
cached).

### F4 — The `trained` signal is inconsistent across streets (HONESTY)

Preflop returns `trained: true/false`. Postflop returns `trained: null`
for the same question (see "HU flop, top set" above). The signal exists
precisely so a caller can tell a real solve from a placeholder; a null
in the middle of that contract forces every consumer to special-case it.

### F5 — Multiway postflop ranges stay narrow (QUALITY)

Path queries cap derived ranges at 6 classes per position, so multiway
postflop answers a thinner question than heads-up. Documented and
measured (11.5s flop / 1.5s turn at cap=6), not a defect — but it is the
gap between "works" and "trustworthy".

### F6 — ~70% of infoset rows are never trained (INHERENT)

Measured in M73 on a 6-max 169-class solve. MCCFR only visits sampled
paths. `trained_mask` reports it honestly per combo. Not fixable by
tuning; noted so it is not rediscovered as a bug.

---

## 4. What is healthy

- **Zero dead public engine functions.** Every non-underscore `def` in
  `poker_solver/` has a caller.
- **Frontend is clean**: 145 tests, no type errors, no lint findings.
- **The engine/API boundary holds**, still enforced by test.
- **Heads-up is genuinely good**: correct, fast, all four streets,
  sane on every check.
- **The honesty signals work.** Every wrong-looking output in this
  diagnostic was either correctly flagged (`trained: false`) or is
  flagged by F2/F4 as a gap in the signal, not a lie by it.

---

## 5. Recommendations, in implementation order

**R1. Make the cache hero-aware (fixes F1).** Include hero's hand *class*
in the path-query cache keys. Class rather than combo: 169 possible
values instead of 1,326, and suit-isomorphic hands share a solve. The
expensive preflop leg is cached separately and stays shared, so the
marginal cost is the postflop solve only (~8-20s). Rejected
alternatives: dropping force-include entirely (cheap, but then most
hands get no advice — the failure we are fixing); widening the cap so
hero is always in range naturally (M24 measured this at hours per
request).

**R2. Stop 9-max returning confident wrong answers (fixes F2).** Two
honest options: raise its budget to per-position parity (~18,000
iterations, ~40 min/spot — unusable live), or **mark 9-max output as
low-confidence in the response** so a consumer can refuse to present it.
Prefer the latter now and the former as an offline/pre-warm path,
because a wrong answer that looks right is the worst failure mode this
product has.

**R3. Widen pre-warm coverage (fixes F3).** Pre-warm the multiway spots
for the stack depths people actually sit at, not just 100bb, in the
existing background thread. Non-prewarmed depths should still work —
they just pay the solve.

**R4. Make `trained` consistent across streets (fixes F4).** Postflop
should return true/false like preflop, never null.

**R5. Widen multiway postflop ranges (addresses F5)** once R1-R4 land
and the cost of a correct cache is known.

---

## 5b. Implementation status

| | Recommendation | Status |
|---|---|---|
| R1 | Hero-aware cache keys | **DONE (M76)** — five path-query caches keyed on hero class; regression test does not clear caches |
| R2 | Stop 9-max looking confident | **DONE (M76)** — `solver_confidence` / `solver_confidence_reason` on `AdviseResponse` |
| R3 | Widen pre-warm | **DONE (M76)** — multiway now pre-warms 100/50/20bb across all table sizes |
| R4 | Consistent `trained` | **DONE (M76)** — `LibraryEntry` carries per-combo flags; library path reports real confidence |
| R5 | Widen multiway postflop ranges | **DONE (M76)** — caps 6 → 8; +14% combos, +43% trained hands, +13% time |

All five shipped. Note R5's measurement also **corrected a stale figure
in `api/config.py`**: the multiway flop cap was documented at 11.5s from
M67, but is 39.7s now — M75's on-demand branch training and M76's
hero-aware cache keys are both correctness fixes that cost real time. The
old number would have made any future latency decision wrong.

## 6. Plan amendment

The v3 roadmap's priority order was: river coverage, then solve speed,
then integrations. River coverage is **done** (M46, M53, M75). Solve
speed is largely done for heads-up and adequate for 3-max/6-max.

**This diagnostic changes the next priority.** The binding constraint is
no longer speed or coverage — it is **correctness under real multi-user
conditions (F1) and not lying to the user (F2, F4)**. R1-R4 come before
any further speed or coverage work, because a fast, wide, wrong answer
is worse than a slow, narrow, honest one.

---

# Round 2 re-audit (same day, after R1-R5)

Same 17 live scenarios, same production settings, against the fixed code.

## Every round-1 product failure is closed

| Scenario | Round 1 | Round 2 |
|---|---|---|
| HU flop, AK overcards | **no advice at all** | advice, `trained: true` |
| HU flop, top set | `trained: null` | `trained: true` |
| All 17 | 1 hard failure, 1 null | all answered, all trained, none uniform |

## What round 2 found that round 1 could not

### N1 — Postflop latency roughly doubled, and that is the price of R1

A heads-up turn went 20.3s → 44.5s, a 6-max flop 21.3s → 45.2s. This is
not a regression to undo: it is the cost of three correctness fixes
landing at once (M75 solving on-demand chance branches instead of
returning them untrained, R1 keying caches on hero, R5 widening the
ranges). A correct 45s answer beats a wrong 20s one — but latency is now
**the binding constraint**, where in round 1 it was correctness.

Preflop numbers also rose (6-max 93s → 139s), but **that path was not
touched by any M76 change**, and this machine has been measured drifting
~1.7x between sessions (M70). Treated as drift, not regression — the
kind of cross-session comparison M70 established is not trustworthy.

### N2 — 9-max is still wrong, now merely labelled

T7s under the gun still returns *call*; AA still shoves. R2 added
`solver_confidence: "low"` so a consumer can refuse to present it, which
is honesty, not a fix. This is the one remaining correctness gap.

### N3 — R6 was tried and is inert; widening the cap is counterproductive

R6's idea: hero only needs its own cache entry when force-inclusion
actually changed the pool, so an in-range hero could share. Implemented,
correct — and **currently a no-op**, because `hero_in_range` requires
hero's combo to be in *every* live position's range simultaneously, which
essentially never happens (measured 0 of 6 hands).

Widening the cap to make it happen was measured and rejected:

| `MAX_PATH_QUERY_CLASSES_PER_SIDE` | in range | combos | six hands total |
|---|---|---|---|
| 6 (shipped) | 0/6 | 85 | 27.0s |
| 12 | 0/6 | 154 | 78.9s |
| 20 | 0/6 | 254 | 217.0s |

No sharing at any width, 8x the cost. The conditional keying is kept
because it is correct and costs nothing, and it starts paying the moment
`hero_in_range` semantics or the cap change — but it is documented as
inert rather than claimed as a win.

## Round 2 recommendations

**R7. Cut multiway postflop latency (addresses N1).** The 40-45s cases
are multiway flop/turn and heads-up turn/river, all of which run a full
solve per request. The per-hero cost is now unavoidable (R1), so the
lever is the solve itself, not the cache.

**R8. Make `hero_in_range` per-position (enables R6).** The current
all-positions AND is the right semantics for the *honesty flag* — hero
either earned its place everywhere or did not — but the wrong input for a
*caching* decision, which only needs to know whether the pool changed.
Splitting the two would make R6 live.

**R9. 9-max: decide between offline pre-solve and removal.** Labelling is
a stopgap. Either pre-solve it properly out of band at the depths that
are pre-warmed, or stop offering the table size.

---

# Round 2 implementation (M78)

**R8 — withdrawn, premise was wrong.** The recommendation said
`hero_in_range`'s all-positions AND was the wrong input for a caching
decision. Reading it again: `all(hero_combo in combo_dict for ...)` is
exactly "no position needed force-inclusion", which *is* the correct
caching signal. It is not wrong semantics — it is simply rarely true,
because the cap keeps the top classes by frequency and a specific hand
usually is not among them. Nothing to fix.

**R7 — done, and the profile named a different culprit than expected.**
Profiling a 6-max flop request (preflop leg warmed and excluded) put
**`nway_combo_equity_vector` at 42.25s of a 42.17s request** — the entire
thing — with **5.1 million `random.sample` calls** inside it. That is the
same shape M68 fixed in `equity._simulate_equity`; `multiway_board_
equity.py` never received the same treatment.

Fixed the same way: sample deck *indices* and gather ranks/suits from two
arrays built once per candidate, instead of sampling `Card` objects and
rebuilding a Python list per sample. Verified **bit-identical** against
stored vectors for flop, turn and river boards — `random.sample` picks
positions in the population, so sampling `range(len(deck))` consumes the
RNG identically. That matters here because this cache's determinism is
part of its contract.

**Measured end to end: a 6-max flop request went 45.2s → 24.6s** at
identical settings (151 combos, cap 8). Roughly 1.8x. Cross-session
comparison, so treat the magnitude as approximate — but the direction is
not in doubt: the profile attributed ~100% of the request to this
function and the change removes 5.1M interpreter-level calls from it.

**R9 — resolved as "keep labelled", not deferred.** 9-max cannot be fixed
by budget; that is measured, not assumed (T7s's fold rate reaches 0.117 at
3,000 iterations and only 0.301 at 9,000, against 6-max's 0.94, and
per-position parity would need ~18,000 iterations at ~40 min/spot). The
two real options were offline pre-solve — which the measurements say would
still be wrong — and removing the table size, which is a product decision
rather than an engineering one. It stays available and marked
`solver_confidence: "low"` with a plain-language reason, which is the
honest state: a real solve of an under-trained problem, flagged so no
consumer presents it as GTO.

---

# Round 3 audit + M79

Re-profiled the same 6-max flop request after M78, and found M78's fix
was **half a fix**.

`nway_combo_equity_vector` was still the whole request, and
`random.sample` was still **5,155,400 calls / 17.9s of 36.3s** — the same
call count as before. M78 removed the per-sample Python *work* (Card
attribute lookups, list rebuilds) but not the calls themselves. Worse,
passing `range(len(deck))` as the population newly introduced **5.1M abc
`isinstance` checks** costing ~5.9s: `random.sample` type-checks its
population, and a `range` is more expensive to check than a list.

**M79 removes the calls entirely.** One vectorized draw per candidate —
random keys plus `argpartition` — gives `remaining_needed` distinct
indices per row in a single numpy op, replacing `samples` interpreter
calls.

This is **not bit-identical**, and could not be: a different sampler
draws different runouts. The contract this module guarantees is
"deterministic given `seed`", which still holds. So it was validated the
harder way, against **exact enumeration** of all 990 two-card runouts:

| samples | mean bias | MAE |
|---|---|---|
| 120 | −0.0076 | 0.0233 |
| 500 | −0.0028 | 0.0125 |
| 2,000 | **−0.0008** | 0.0056 |

Bias shrinking toward zero and MAE falling as 1/√n is the signature of an
unbiased estimator — stronger evidence than bit-identity would have
given, since bit-identity only proves two implementations agree, not that
either is right.

**Cumulative effect on a 6-max flop request: 45.2s → 24.6s (M78) →
19.9s (M79).** The turn is 4.0s.

A test asserting the N-way path matched the pairwise table *exactly* at
N=2 now asserts statistical agreement at 4,000 samples. The exact match
had only ever held because both implementations consumed the same RNG
stream — a coincidence of shared plumbing, not evidence they agree about
poker. Added a companion test proving the 1-card-runout path is
enumerated rather than sampled (two different seeds must agree exactly).

---

# Round 4 audit + M80

Re-profiling after M79 showed a transformed picture: **8.2M function
calls, down from 94.5M**. The interpreter overhead was gone and the
remaining cost was genuine numpy work — `best_hand_rank_batch` at 16.1s
of 21.4s (75%).

What was left was structural, not overhead. Every candidate dealt its own
runouts (excluding its own two cards), so the **k opponent hands were
re-ranked once per candidate**: work of `candidates x samples x (1 + k)`
where `samples x (candidates + k)` suffices. At 120 candidates against 2
opponents, ~3x more hand evaluations than necessary.

**M80 shares one set of runouts across all candidates**, ranking the
opponents once, and has each candidate ignore the ~8% of samples that
collide with its own cards. The same trade M68 made in
`equity._simulate_equity_shared_board`: a bounded variance cost, not a
bias one, since which runouts a candidate blocks depends only on its own
cards and never on how well it does.

## A bias scare that was under-powered measurement

The first validation showed bias at 2,000 samples of **−0.0035 and not
shrinking**, against M79's −0.0008 — which looks exactly like a real
estimator bias introduced by the collision masking. It was not. That
figure came from 8 seeds x 5 candidates = 40 measurements, whose standard
error is large enough to produce it by chance.

Re-measured with 24 seeds (168 measurements) and standard errors
reported:

| samples | bias | SE | \|bias\|/SE | MAE |
|---|---|---|---|---|
| 2,000 | −0.00049 | 0.00065 | 0.7 | 0.0064 |
| 6,000 | −0.00002 | 0.00036 | **0.1** | 0.0036 |

Not significant at either count, with MAE falling as 1/√n. The estimator
is unbiased. This is the "one reading is not a measurement" lesson
(M49, M54, M70, M71) in its statistical form: **an effect size without a
standard error is not a finding**, and it nearly cost a correct
optimization.

## Cumulative effect

A 6-max flop request, identical fidelity throughout (151 combos, 63
trained):

| | flop | turn | river |
|---|---|---|---|
| before R7 | 45.2s | 10.4s | — |
| M78 | 24.6s | — | — |
| M79 | 19.9s | 4.0s | — |
| **M80** | **14.4s** | **2.5s** | **1.0s** |

**3.1x faster end to end**, with the equity estimator validated against
exact enumeration at every step rather than assumed.

---

# Round 5 audit + M81

With postflop down to 14.4s, the remaining latency was the **6-max
preflop solve (~91s cold)** — pre-warmed for three depths, paid in full
for any other. Profiling it put `best_hand_rank_batch` at **74.7s of
102.5s (73%)**, which is real work. But two pieces inside it were pure
overhead:

- **`np.array(_FIVE_CARD_COMBOS)` rebuilt on every call** — the same
  (21, 5) index array constructed from a Python list of tuples **7,595
  times** in one solve.
- **`_pack_scores` looping five `.astype(np.int64)` calls** on arrays
  that were *already* int64 (they are slices of the int64 lookup tables).
  Every one was a copy to the type it already had: **45,570 astype calls,
  6.7s**, inside a function costing 12.35s of 102s.

M81 hoists the index array to import time and replaces the loop with one
matrix-vector product against precomputed positional weights.

**Identical output, verified**: the old and new batch paths produce
bit-equal score arrays, and the batch ordering still agrees with the
scalar `best_hand_rank` path on random 7-card hands.

Interleaved in-process A/B: **1.20x on hand evaluation**. End to end,
6-max preflop **91s → 75s** and the flop **14.4s → 11.5s**.

## Cumulative across rounds 2-5

| | flop | turn | river | preflop (cold) |
|---|---|---|---|---|
| before R7 | 45.2s | 10.4s | — | ~91s |
| M78 | 24.6s | — | — | — |
| M79 | 19.9s | 4.0s | — | — |
| M80 | 14.4s | 2.5s | 1.0s | — |
| **M81** | **11.5s** | — | — | **75s** |

**~3.9x on the flop**, at identical fidelity (151 combos), with the
equity estimator validated against exact enumeration and the hand
evaluator against its own scalar path at every step.
