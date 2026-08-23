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

---

# Round 6 audit + M82

Re-ran the 17-scenario live simulation. **All 17 answer, all trained,
none uniform**, and rounds 2-5's speed work shows up end to end:

| scenario | round 1 | round 6 |
|---|---|---|
| HU flop, AK overcards | **no advice** | 8.4s, trained |
| HU turn / river | 20.3s / 15.8s | 18.6s / 12.7s |
| 6max flop / turn / river | 21.3 / 4.5 / 3.5s | **12.2 / 2.3 / 0.9s** |
| 6max preflop | 93s | **76.7s** |
| 9max preflop | 126s | **106.7s** |
| 6max preflop 30bb | 66s | **55.8s** |

## F7 — the honesty signal never reached the user

The round-6 finding is not in the table. M76 added `solver_confidence` so
a caller could refuse to present 9-max advice as GTO. **Nothing in the
frontend read it.** A 9-max user saw a confident-looking strategy with no
indication the backend already knew it was unreliable.

That is the exact failure mode this project's honesty machinery exists to
prevent, surviving *because* the machinery was only half-wired: the
signal was produced correctly and then dropped at the API boundary. Worth
naming as a class of bug — **a correctness signal that no consumer reads
is not a correctness signal** — because `trained`, `range_confidence` and
`source` all have the same exposure, and only the first two are rendered.

**M82 wires it through**: `solver_confidence` / `solver_confidence_reason`
on the response type, and a `role="alert"` warning in `AdviseSolver`
carrying the backend's own plain-language reason rather than a generic
label. Styled deliberately louder than the existing `.depth-hint` grey —
`trained` and range confidence describe how much was computed and can
reasonably be skimmed; this one says the answer may simply be wrong.

Two tests pin it: one that the warning appears with its reason, one that
it is **absent** at `solver_confidence: "high"` — a warning that is
always present is furniture, and users learn to ignore furniture.

---

# Round 7 audit + M83

M82 named a class of bug — *a correctness signal no consumer reads is not
a correctness signal* — so round 7 audited that class systematically:
every `AdviseResponse` / `HeroAdvice` field against what the frontend
actually renders. Two findings, the second worse than the first.

### F8 — `hero.trained` was computed and never shown

The single most direct honesty signal in the product: whether **your**
hand's numbers came from real solving or are the uniform placeholder. It
was in every response and rendered nowhere, so a placeholder displayed
identically to a solved strategy. M75 measured multiway turn/river
returning `0/132 trained` — a user in that state saw `0.333 / 0.333 /
0.334` presented as advice.

`ComboRow` had accepted a `trained` prop since M59. Nothing passed it.

### F9 — the fallback message was actively false

```tsx
{result.hero.strategy ? ( ...advice... ) : (
  <p>No decision to make here — the hand resolved before this street.</p>
)}
```

That fired whenever hero had no strategy — including when there *is* a
live decision and we simply have nothing for this particular hand. The
user was told their hand was over when it wasn't. Worse than a missing
signal: a missing signal omits information, this one supplies wrong
information, and the response already carried `is_terminal` to tell the
two apart.

**M83 fixes both**: hero's `trained` flows into `ComboRow` and raises a
loud warning when false; the fallback branches on `is_terminal` and says
"there is a decision here, your hand wasn't in the solved range" when the
hand is live.

Three tests, including both halves of each fix — that the "hand resolved"
message still appears when the hand genuinely resolved, and that the
low-confidence warning is absent at `"high"`. A message that is always
present teaches users to ignore it.

---

# Round 8: full diagnostic (broadened simulation)

The earlier harness used 17 spots, mostly premium/trash hands, on one dry
board. Real play is not that. Round 8 broadened it to 25 spots covering
**board texture** (dry rainbow, wet connected, monotone, paired, ace-high),
**hand types the old set never touched** (flush draws, weak made hands,
bluff candidates), **action lines beyond raise-call** (3-bet pots, limped
pots, facing a raise, facing a 3-bet), and **stack depths** from 200bb to
15bb.

## Code audit

| | |
|---|---|
| Backend tests | 768 passing |
| Frontend | 150 tests, `tsc` clean, `oxlint` clean |
| TODO / FIXME / HACK markers | **0** across engine, API and frontend |
| Dead public engine functions | 0 |
| Registered caches | 15, all via the M60 self-registering registry |
| Deprecated routes still served | 5 (functional, superseded by `/advise`) |

Structurally healthy. `api/solving.py` has grown to 1,882 lines — the
largest file — but it is the orchestration layer and the growth is
per-cell logic, not duplication.

## F10 — `/advise` can only answer the FIRST decision on each street (SEVERE)

The broadened simulation returned `call_or_check` as the top action for
**every** postflop scenario — every texture, every hand strength. That
first looked like a degenerate solver. It is not: the node being answered
is always BB first-to-act after calling a raise, where checking the whole
range genuinely *is* correct GTO (there is no fold action; checking is
free).

The real problem is why that is the *only* node reachable:

```
flop, first to act        -> 200 OK
flop, after villain checks-> 422 "flop_action_path was supplied without a turn_card"
flop, facing a bet        -> 422 "flop_action_path was supplied without a turn_card"
```

**A player facing a bet on the flop cannot get advice.** That is the most
common and most consequential decision in poker, and it is unreachable.
The same holds on the turn. `/advise` answers "what do I do first on this
street", not "what do I do now" — which is the product's entire stated
purpose.

This is an *addressability* gap, not a solver gap: the solve already
covers the whole flop subtree, and `_resolve_action_path` already walks a
path to an arbitrary node (the turn cell uses it to find the flop
terminal). The data exists and is thrown away.

**Why no earlier round caught it:** every scenario I had written asked
about the first decision, because that is the only shape the API accepts.
The harness was shaped by the API's own limitation — a reminder that a
test suite written against an interface cannot discover what the
interface refuses to express.

## F11 — sanity checks were too weak to catch F10's symptom

The `sane` predicates only ever checked fold frequencies. A strategy of
"check 100% with everything" passes every one of them. Round 8 added
distribution-level inspection and that is what surfaced the uniformity.

## Recommendations

**R10. Make every decision addressable (fixes F10).** Accept
`flop_action_path` without `turn_card`, resolving to whatever flop node
that path reaches, and `turn_action_path` without `river_card` likewise.
Reject only paths that reach a terminal (nothing to advise) or are
illegal. This is the difference between a street-opening advisor and the
live-table advisor this project set out to build.

**R11. Assert on distributions, not just fold rates (fixes F11).** A
scenario check that cannot distinguish "checks everything" from real play
is not a check.

## F12 — the two flop decisions model different games

Found while writing R10's tests. The **opening** flop decision is served
by the canonical library at `solve_flop`'s defaults — raise sizes
`(2.5, 3.0, 2.2)`, `max_raises=4`. Any **later** flop decision is served
by `solve_flop_turn` at `FLOP_TURN_RAISE_SIZES=(2.5,)`,
`FLOP_TURN_MAX_RAISES=2`.

Same street, same hand, two different trees. A user offered
`raise:12.50` at their first decision can find that action does not exist
one decision later. Nothing is *wrong* in either answer — each is a
correct solve of the game it models — but they are not the same game, and
the product presents them as one continuous street.

Deliberately **not** fixed in M84. Aligning them means either giving the
mid-flop cell its own solve at `solve_flop`'s config (losing the shared
turn cache, so a flop-then-turn user pays twice) or retuning the library's
tree (invalidating every stored canonical entry and changing every
heads-up flop answer the product currently gives). Both are real changes
needing their own measurement pass; neither is something to land
untested alongside the feature that exposed it. Recorded as R12.

## Round 8 recommendations

**R10 — DONE (M84).** `/advise` now answers any flop decision, not just
the street's opening one. Shares the turn cell's solve and cache, so a
player who asks about a flop decision and then the turn pays for one
solve. A path that reaches a terminal is rejected with a message pointing
at the turn rather than being answered with a fabricated node.

Behaviour, on a real heads-up flop (2h6d9c), previously unreachable:

| node | set of nines | air (5c4d) |
|---|---|---|
| first to act (BB) | check 0.99 | check 1.00 |
| after a check (BTN) | check 0.95, shove 0.05 | check 1.00 |
| **facing a bet (BTN)** | **shove 1.00** | **fold 1.00** |

The hand-strength discrimination that rounds 1-7 never saw, because the
node where it happens was not addressable.

**R11 — DONE (M84).** The simulation now inspects distributions, not just
fold rates. The old predicates passed a strategy of "check 100% with
everything".

**R12 — open.** Align the two flop trees (F12), or accept and document
the discontinuity at the API surface so a consumer can see which tree
answered.

**R13 — open.** Extend R10 to the turn: `turn_action_path` without a
`river_card` is still rejected, so turn decisions after the first have
the same gap the flop just lost.

---

# R13 implementation (M85)

The turn had the same gap M84 removed from the flop. The turn cell read
`chance_node.branches[turn_card].root` and its own comment described
exposing only that node as "a deliberate cut, not an oversight". It was
the same cut, wrong for the same reason: **a player facing a bet on the
turn could not ask.** The subtree was already solved.

`turn_action_path` is now accepted on a turn query and resolved into that
subtree, rejecting only paths that reach a terminal (nothing left to
advise — the message points at the river).

Turn behaviour on 2h6d9c + Kd, previously unreachable:

| node | set of nines | air (5c4d) |
|---|---|---|
| first to act (BB) | check 0.79, raise 0.18 | shove 0.43, raise 0.57 |
| after a check (BTN) | **shove 0.83** | check 0.90 |
| facing a bet (BTN) | **call 1.00** | **fold 1.00** |

Value and give-up separate correctly, and the first-to-act row shows the
check-raise / bluff structure a real solve produces.

**Multiway turn paths are refused, not silently mis-answered.** That cell
reads its node off a *sampled* chance branch and needs its own pass; a
422 saying so beats a plausible answer about the wrong node — the lesson
F2 and F10 both taught.

## Coverage after M84 + M85

| street | first decision | later decisions |
|---|---|---|
| preflop | yes | yes (action path always supported) |
| flop, heads-up | yes | **yes (M84)** |
| turn, heads-up | yes | **yes (M85)** |
| river, heads-up | yes | no |
| flop/turn, multiway | yes | no |

"Advice at any point" is now true for the whole heads-up tree except the
river's later decisions, and for the opening decision of every multiway
street.

---

# R14 implementation (M86) — heads-up coverage is complete

The river's later decisions were the last unreachable nodes in the
heads-up tree, and facing a river bet is the largest single decision in a
hand. `AdviseRequest` gains `river_action_path`, resolved into the
already-solved river subtree exactly as M84 and M85 do for their streets.

River behaviour on 2h6d9c + Kd + 4s, previously unreachable:

| node | set of nines | air (5c4d) |
|---|---|---|
| first to act (BB) | **shove 0.91** | check 0.85, **bluff 0.15** |
| after a check (BTN) | **shove 0.94** | check 0.90 |
| facing a bet (BTN) | **call 0.95** | **fold 1.00** |

Value, bluff and give-up all separate — the structure a real river solve
produces, and none of it was previously visible.

The new field gets the same contradiction guards its siblings have
(rejected without a `river_card`, and without a board). A field that is
silently ignored is the quietest kind of wrong.

## Coverage after M84-M86

| street | first decision | later decisions |
|---|---|---|
| preflop | yes | yes |
| flop, heads-up | yes | **yes (M84)** |
| turn, heads-up | yes | **yes (M85)** |
| river, heads-up | yes | **yes (M86)** |
| flop/turn/river, multiway | yes | no — refused with a clear 422 |

**Every heads-up decision in a hand is now reachable.** That is the
"advice at any point" the project set out to build, true for the first
time on the two-player tree. Multiway still answers only each street's
opening decision, and says so rather than answering the wrong node.

## Remaining open

- **R12** — the two flop trees still differ (F12).
- **R15** — extend M84-M86 to multiway. Those cells read their nodes off
  *sampled* chance branches rather than an exhaustively-solved tree, so
  it is a different problem, not a copy of the same fix.

---

# R15, part 1 (M87) — multiway flop decisions

M84-M86 made every heads-up decision reachable. Multiway still answered
only each street's opening one. The **flop** extends cleanly and needed
no new solve: `solve_flop_multiway` already returns a `StrategyResult`
over the whole flop tree (flop-only — no chance dispatch), so a deeper
decision is already solved for. Resolving into it is a lookup.

6-max, three live players, on 2h6d9c with a set of nines:

| node | position | strategy |
|---|---|---|
| first to act | UTG | check 0.58, raise 0.42 |
| after a check | MP | check 0.57, raise 0.35, shove 0.08 |
| **facing a bet** | MP | **call 0.99**, fold 0.01 |

**The turn and river deliberately do NOT extend this way**, and the
reason is structural rather than effort: those cells read their node off
a *sampled* chance branch, so the node a client asks about may never have
been built. M75 already had to solve on-demand branches there just to
make the street's first decision real. Reaching a deeper one means
building and training a subtree that MCCFR never sampled — a different
problem, and one that should be measured on its own rather than assumed
to be a copy of this fix. They keep refusing with a clear 422.

## Coverage after M84-M87

| street | heads-up | multiway |
|---|---|---|
| preflop | any decision | any decision |
| flop | **any decision** | **any decision (M87)** |
| turn | **any decision** | first only — refused clearly |
| river | **any decision** | first only — refused clearly |

## Remaining open

- **R12** — the two flop trees still differ (F12).
- **R15 part 2** — multiway turn/river later decisions, needing the
  on-demand-branch work described above.

---

# R12 implementation (M88) — one tree per street

Two corrections to F12's own write-up, found by checking rather than
trusting it:

1. It claimed fixing this meant "invalidating every stored canonical
   entry". **There are no stored entries.** `_path_query_libraries` is an
   in-memory `_SolveCache` rebuilt per process; the only thing on disk is
   `preflop_equity.npy`. That risk did not exist.
2. It framed the choice as consistency *versus* cost. The opposite is
   true: `solve_flop` is flop-only (runouts averaged at the terminal)
   where `solve_flop_turn` chains a real turn, so **the consistent option
   is also the cheaper one** — a later flop decision measured 7.5s
   against the opening decision's 11.2s library miss.

**M88 points the mid-flop cell at `solve_flop` with the library's own
config.** Both flop decisions now offer the same actions:

| | opening decision | later decision |
|---|---|---|
| before | `call`, `raise:12.50`, `all_in:100.00` | `call`, `all_in:97.50` |
| after | `call`, `raise:12.50`, `all_in:100.00` | `call`, **`raise:12.50`**, `all_in:97.50` |

What it gives up is the shared turn cache: a user asking about a mid-flop
decision *and* the turn now pays two solves. That is the right trade —
the doubled cost hits only users who ask both questions, while the
inconsistency hit everyone who asked twice on one street.

## F13 — the library solves at a bucketed stack depth

The residual difference above is the all-in *size*: `100.00` at the
opening decision, `97.50` one later. Not a tree mismatch — the library
canonicalizes stack depth into **5bb buckets**, and
`canonical_stack_depth(97.5) == 100.0`. So a real 97.5bb spot is solved
at 100bb, and the response reports `effective_stack_bb: 97.5` while
offering `all_in:100.00`. A user cannot shove 100 holding 97.5.

Deliberately not "fixed": the bucketing is the mechanism that makes
canonical reuse work at all, and it is the library's entire reason for
existing. Relabelling the action to the real stack would make the number
honest while leaving the strategy behind it computed for a different
depth — a worse kind of wrong. Documented in CLAUDE.md instead: the
action **kind** is right, the size can overstate by up to one bucket.

---

# R15 part 2 (M89) — multiway turn and river depth

M87 said the multiway turn and river could not extend the way the flop
did: they read their node off a **sampled** chance branch, where the node
a client asks about may never have been built. That was accurate when
written, and had **already stopped being true**.

**M75 trains the on-demand branch.** `ensure_mccfr_chance_branch` runs
`mccfr_solve` over the branch's own subtree and merges the result into
`result.node_data`, so every node inside it is solved for. The blocker
M87 named had been removed two milestones earlier by work done for a
different reason, and nobody went back to check. Enabling it needed no
new machinery — only asking.

Worth recording as a pattern: **a limitation documented as structural can
quietly become false.** M87's note was careful, correct at the time, and
wrong within two milestones. It was also the *reason* the capability
wasn't attempted, so the stale note cost real coverage.

Measured at production settings, 6-max with three live players (the
shrunk probe config returned untrained uniforms — a probe artifact, and
checking that distinction is M75's lesson running both ways):

| node | range trained | hero | strategy |
|---|---|---|---|
| turn, first to act | 56/151 | yes | check 0.70, raise 0.29 |
| turn, after a check | 51/151 | yes | raise 0.65, shove 0.34 |
| **turn, facing a bet** | 51/151 | yes | **call 1.00** |
| river, first to act | 56/151 | yes | raise 0.96 |
| **river, facing a bet** | 51/151 | yes | **call 0.99** |

Deeper decisions cost **0.0s** — they reuse the branch solve the first
decision already paid for.

## Coverage after M84-M89 — the arc is complete

| street | heads-up | multiway |
|---|---|---|
| preflop | any decision | any decision |
| flop | any decision | any decision |
| turn | any decision | **any decision** |
| river | any decision | **any decision** |

**Every decision in a hand is now reachable, at every supported table
size.** That is the "advice at any point" this project set out to build
in its v3 vision, and it is true for the first time.

A test that asserted multiway turn paths were *refused* now asserts they
work — the refusal was the documented behaviour, and documenting it did
not make it necessary.

---

# Round 9 — playing a whole hand

Every previous round asked isolated questions. With M84-M89 making every
decision reachable, round 9 ran the test a player actually performs: walk
a full hand, asking for advice at each decision until it ends.

**A complete heads-up hand now plays through — seven decisions, every one
trained**, holding a set of nines:

| decision | position | advice |
|---|---|---|
| preflop | BB | 3-bet to 7.50 |
| flop, 1st | BB | check 0.99 |
| flop, 2nd | BTN | raise 12.50 |
| turn, 1st | BB | check 0.79 |
| turn, 2nd | BTN | shove 0.83 |
| river, 1st | BB | shove 0.91 |
| river, 2nd | BTN | shove 0.94 |

That is the product working end to end for the first time. Deeper
decisions on a street cost **0.0s** — they reuse the solve the street's
first decision paid for.

## F14 — the action-path contract is contradictory, and the error didn't say so

The first attempt at this walkthrough failed at five of nine steps, and
**every failure was my own harness's fault** — which is exactly the
finding. `flop_action_path` has *opposite* requirements depending on
which street is being asked about:

- asking about a later **FLOP** decision → it must **not** close the street
- asking about the **TURN** → it **must** close the street

Same field, same hand, contradictory rules. The old error read *"does not
reach a terminal — action isn't capped yet"*: a true statement about the
tree that teaches the caller nothing about either rule, using a word
("capped") that appears nowhere in the request.

If the contract can catch the person who *wrote* the endpoints, it will
catch every integrator. For a product other people build against, an
error that doesn't explain the rule it enforces is a defect in its own
right — not a documentation gap.

**M90 rewrites all six of these messages** to name the real problem and
both ways out:

> flop_action_path does not close the flop's betting, so no turn card can
> be dealt yet. Two different things are being asked for here and it is
> easy to mix them up: to ask about a TURN decision, flop_action_path
> must run to the end of the flop's action; to ask about a later FLOP
> decision instead, send the same partial path WITHOUT a turn_card.

A test pinning the old wording now pins the new, keeping its actual
intent (the error names the *client's* field, not an internal one).

---

# Round 10 — full hands at multiway and short stacks, plus input robustness

## Full-hand walkthroughs

Round 9 walked one heads-up hand at 100bb. Round 10 walked three more,
across the dimensions that were untested: multiway, and stack depths
where the game changes character. **All three played through completely
— 21 decisions, every one trained, no failures.**

| hand | notable |
|---|---|
| 6-max, 3 live, 100bb, set of nines | value line: check → raise 22.5 → raise → shove 0.71 |
| heads-up, 30bb, top pair | middling line, river shove 0.52 |
| heads-up, **15bb**, AK | **preflop shove 1.00** — correct push/fold play |

The engine adapts to depth rather than replaying one strategy: 100bb
plays small pots, 15bb shoves. Deeper decisions on a street cost 0.0s,
reusing the street's first solve.

## Input robustness — never audited before

A live-table advisor is integrated against by clients that send
malformed, impossible and hostile input. 18 such requests, none of which
should produce a 500 or a silently wrong answer.

**16 of 18 gave a clear 422 with an explanatory message.** No crashes, no
500s. Rejected correctly: duplicate hero cards, nonsense card text,
negative/zero stacks, unsupported player counts, illegal action names, a
500-step action path, a turn card already on the board, a river without a
turn, out-of-range iteration counts.

## F15 — an impossible board was answered confidently

Two cases returned 200. One is defensible (a 1e9 stack is absurd but not
impossible). The other is a real bug:

```
board "2h2h9c"  -> 200 OK, 143 combos
board "AsAsAs"  -> 200 OK, hero advice: call 1.00
board "2h6d2h"  -> 200 OK, raise 0.58
```

**A board naming the same card twice cannot exist, and the product
answered anyway** — three aces of spades produced a confident `call
1.00`. Hero's own two cards had always been validated ("HandCombo needs
two distinct cards") and a turn card colliding with the board was caught
downstream. The gap was specifically the flop's cards *against each
other* — and per-field validation is exactly how that pairing got missed.

**M91 checks every card the request names in one place** — board, turn,
river and hero together — because every pairing among them is equally
impossible:

> `As appears twice in the same field — a card can only be in one place.`
> `9c appears in both board and hero_cards — a card can only be in one place.`

This is the same failure mode the whole diagnostic arc keeps turning up,
and the least detectable one: **a real answer to an impossible
question**, where nothing in the response looks wrong. Four tests pin it,
including one that a legitimate board still answers — a guard that fires
on everything is as useless as one that never fires.

---

# Round 11 — concurrency

Never audited, and overdue: the worst bug this whole diagnostic found
(M76's cache key) was a **multi-user** failure, and M75 made on-demand
branch training *mutate* a cached `StrategyResult` in place.

## Correctness under concurrency: clean

22 concurrent requests — 12 on one spot across three hero hands, 10
across different turn cards that mutate shared cached results. **Zero
errors, zero 500s, and every repeated question got a consistent answer.**
The locking disciplines hold, including M75's in-place mutation.

## F16 — a thundering herd on every cold spot

Part B took 277s for 10 requests that share **one** underlying solve (the
turn card is not in the cache key — one `solve_flop_turn` covers every
turn card via `chance_data`). That is not what a shared solve should
cost, so it was measured directly:

```
8 CONCURRENT requests, same solve key:  223.3s   solve_flop_turn calls = 8
8 SEQUENTIAL requests, same key:          0.0s   solve_flop_turn calls = 0
```

**Eight full solves for a question that needed one.** Every cache helper
checked the cache, computed *unlocked*, then wrote — and `caches.py`
documented that race as an accepted tradeoff on the grounds that these
solves are deterministic, so whichever racer wins is correct.

That reasoning is sound **about correctness** and it is exactly right.
What it never addressed is **cost**, and nobody had measured it. N
simultaneous users on a cold spot did N times the work — the same
multi-user shape as M76's bug, hiding behind a comment that had already
considered the correctness question and stopped there.

**M92 adds `_SolveCache.get_or_compute`** — the single-flight pattern —
and routes the expensive solves through it. The lock discipline is the
part worth getting right: `self.lock` guards only the dict and the
per-key lock registry, never a solve, or concurrent misses on *different*
keys would serialize for no reason. Per-key locks are dropped once their
solve lands, so a long-running server does not accumulate one per key
ever seen.

```
8 CONCURRENT requests, same solve key:   31.7s   solve_flop_turn calls = 1
```

**223.3s → 31.7s, 8 solves → 1.** Applied to the heads-up turn cell, the
mid-flop cell, and the multiway preflop solve — the last being the worst
possible herd at 75-140s per redundant copy.

`api/caches.py` also gains its own test module, which it never had. Its
behaviour was only ever covered incidentally through the endpoints using
it — which is precisely how this cost went unmeasured. Six tests, including
that different keys do **not** serialize (trading one performance bug for
another), that per-key locks do not leak, and that a failing solve leaves
the key retryable rather than poisoned.

---

# Round 12 — the long-running process

Every audit so far measured a fresh process. A real server runs for
weeks, and **no cache evicted anything**: every entry lived for the life
of the process.

## F17 — memory grows without bound, and entry count hides it

Measured with `tracemalloc` over varied traffic:

```
requests  entries   heap MB
       5        5       0.4
      10        5       0.7
      15        5       1.0
      20        5       1.3
      25        5       1.6
```

The instructive part is that **entry count stayed flat at 5 while the
heap kept growing**. Counting entries — the obvious metric, and the one
`__len__` exposes — would have shown a perfectly stable cache. The growth
is *inside* the entries: `_path_query_libraries` maps a partition key to
a **library dict that itself accumulates** canonical spots forever. Two
unbounded dimensions, one of them invisible to the metric you would
naturally reach for.

At ~0.065 MB per request, 100k requests is several GB. A long-running
server was going to exhaust memory — the one failure mode a cache is not
allowed to have.

**M93 gives `_SolveCache` a `maxsize` with LRU eviction**, applied to all
13 solve caches. `_multiway_cache` and `_preflop_raw_cache` stay
unbounded **on purpose**: a few dozen entries, 75-140s each to rebuild,
filled by the startup pre-warm — evicting one throws away work the server
did specifically so a user would not wait for it.

LRU rather than FIFO or TTL, because the value here is recency-shaped:
a spot asked about again is exactly what makes it worth keeping, and a
solve never goes stale (same inputs, same answer tomorrow). Reads go
through `get`, which marks recency — reading `.entries` directly would
not, and a hot entry would age out as if cold.

## A deadlock I introduced, and what it says about the API

Converting the 11 direct `.entries[key] = value` writes to `store()`
**hung the entire test suite**. Every one of those writes sat inside an
existing `with cache.lock:` block, and `store()` takes the lock itself —
`threading.Lock` is not reentrant.

The fix is one line (`RLock`), but the lesson is about the class: it
deliberately exposes `.entries` and `.lock` so call sites can choose
their own locking discipline, and that flexibility is what made it
possible to call a locking method from inside a lock. Reentrancy makes it
safe from either side rather than requiring every caller to know which
side it is on. The per-key single-flight locks stay non-reentrant on
purpose — reentrancy there would silently defeat the gate.

Pinned by a regression test, because the failure mode is a **hang** with
no error and no indication of where to look.

---

# Round 13 — running the actual app in a browser

Every frontend test stubs `fetch`. **Nobody had ever run the real UI
against the real API.** For a product, that is the gap that matters most.

Built the frontend, started the API (which serves `dist/` at `/`), and
drove it in a browser.

## What works

- The app loads and answers. Heads-up preflop returned real advice for
  AsAh in 3.6s. **No console errors.**
- **M82's low-confidence warning renders correctly.** A 9-max user sees
  AA shoving 81% — wrong advice — with the warning right beside it:
  *"Low confidence. 9-max preflop does not converge at any affordable
  budget... Treat this as a hint, not GTO."* The honesty chain works end
  to end for the first time: solver → API → UI → user.

## F18 — every mid-street decision was unreachable from the UI

With a board entered, the Advisor offered **no flop action controls at
all**. The only options were "Get advice" (the street's opening decision)
or advancing to the next street. The UI could ask *"what do I do first on
this street"* and nothing else.

That is precisely the limitation M84-M89 removed from the API — **and I
introduced it**, by shipping four milestones of capability without ever
checking whether a user could reach it. It is the same pattern as M82's
unrendered `solver_confidence`, which I had already written up *as a
pattern*, and then repeated.

**M94 adds a "Your spot" selector** — *I'm first to act* / *They checked
to me* / *I'm facing a bet* — sending a **partial** action path for the
street being asked about. Deliberately three fixed options rather than a
free-form action builder: they cover what a player needs to describe,
stay legal at any live-position count, and need no legal-action walker.

Verified in the browser, with the discriminator that matters — the same
hand at two spots produces structurally different action sets:

| spot | air (5c4d) |
|---|---|
| I'm first to act | check 100% (no fold exists — checking is free) |
| **I'm facing a bet** | **fold 73%**, call 22%, shove 5% |

Fold only exists at the second, which is what proves a genuinely
different node was reached.

The tests assert the **request body**, not just that a control renders —
a selector that looks right and sends the wrong path is exactly the
failure this round found.

---

# Round 14 (M95) — F13 was not an edge case, and it was fixable

R14 recorded F13 — the library canonicalizes stack depth into 5bb
buckets, so a 97.5bb spot is solved at 100bb and can be told to shove
100 — and closed it as **deliberately not fixed**: bucketing is the
library's whole reason for existing, and relabelling the action to the
real stack would leave a number that looked honest in front of a
strategy computed for a different depth.

Both halves of that were wrong.

## It is the default case, not a 97.5bb curiosity

Swept across stacks and preflop lines instead of the single spot R14
happened to look at:

| stack | preflop line | behind | offered |
|---|---|---|---|
| **100** | **limp, check** | **99.0** | **`all_in:100.00`** |
| 100 | raise, 3bet, call | 92.5 | `all_in:90.00` |
| **60** | **limp, check** | **59.0** | **`all_in:60.00`** |
| 43 | raise, 3bet, call | 35.5 | `all_in:35.00` |

The failing case is the **default stack size on the simplest preflop
line there is**. Any round starting stack that pays a blind is one bb
short of its own bucket. R14 found this at 97.5 and generalized in the
wrong direction — toward "unusual depth" rather than toward "every pot
that isn't raised."

## The third option R14 didn't consider

R14 weighed two: keep bucketing (unaffordable advice) or relabel the
action (honest number, wrong strategy behind it). There is a third —
**round the bucket down instead of to nearest**. Then `canonical <=
real` is an invariant, and *every* size the tree derives is affordable
by construction, the all-in and every raise alike. Nothing is
relabelled; the number and the strategy still agree with each other.

The cost is that worst-case depth error doubles, half a bucket to a
full one. Measured, across every node of a real flop solve, as mean
total-variation distance from a solve at the true depth:

| SPR | truth vs floor | truth vs nearest |
|---|---|---|
| 9.9 | 0.0083 | 0.0011 |
| 2.3 | 0.0000 | 0.0000 |
| 1.3 | 0.0000 | 0.0000 |
| 0.6 | 0.0014 | 0.0009 |

Floor is the worse approximation — by under one percent of probability
mass at its worst, and indistinguishable at three of four depths. That
is well inside the noise the solve already carries from Monte Carlo
equity and untrained combos. **Advice that cannot be followed is a
different category of wrong from advice that is slightly off**, and R14
priced the two as if they were the same category.

Bucket *count* is unchanged, so library hit rate is unaffected.

## The obvious repair reintroduced the bug

A bare floor sends anything under one bucket to 0.0, which is not a
game. Clamping up to one bucket instead — and the sweep test caught it
immediately: an 8bb stack in a raise-3bet-call pot leaves **0.5bb**
behind and was offered **`all_in:5.00`**. The exact bug, in the exact
fix for it, one line later.

Sub-bucket stacks are now used unbucketed. Reuse is lost for those
depths and costs nothing — a player with under 5bb behind barely has a
decision — and the invariant then holds with no exception at all.

## Why the sweep, not an example

The old rounding passed every hand-written example anyone had thought
to write, including the ones in `test_canonicalize.py`. Both new tests
sweep: 4,000 depths x 4 bucket sizes at the function, and 18
stack-by-line combinations at the `/advise` boundary. Asserting at the
response boundary matters as much as at the rounding function — that is
where a user sees it, and a future change anywhere between the two would
otherwise reintroduce it silently.

**The pattern, which is R14's real lesson:** "inherent to the design,
not a bug to fix casually" is a conclusion that has to be earned by
enumerating the options, and R14 enumerated two of three. Writing the
constraint down made it durable — it sat in CLAUDE.md as settled for
long enough that later rounds read past it.

---

# Round 15 (M96) — the file every session reads was wrong

M95's lesson was that a constraint written down as settled gets read
past. The obvious next question is what *else* is written down as
settled. So: audit CLAUDE.md itself — the one document loaded into every
session as **current state**, and the only one nothing verified.

## Three of four config claims had drifted

| CLAUDE.md said | `api/config.py` has |
|---|---|
| `MAX_MULTIWAY_*_CLASSES_PER_POSITION = 6` | **8** |
| `MAX_PATH_QUERY_CLASSES_PER_SIDE = 6` | **10** |
| `MAX_TURN_PATH_QUERY_CLASSES_PER_SIDE = 2` | **4** |
| `MULTIWAY_BRANCH_TRAIN_ITERATIONS = 100` | 100 ✓ |

These are not decoration — they are the numbers a reader reasons about
cost and range width from. The two `_PER_SIDE` ones sit in narrative
about what M24 and M26 shipped, where they were true *at the time*; they
now read as current to anyone who wasn't there. Rewritten as "6 at the
time".

## The file contradicted itself, twice

- The constraints list credited `_simulate_equity_shared_board` with
  **1.95x**. Twenty lines later, "Measuring performance" says that exact
  number was **withdrawn in M70** as an invalid cross-session comparison.
  The file cites its own retraction and keeps the retracted figure.
  Now quotes M70's interleaved **6.06x**.
- The `trained`-flags entry says M76 fixed the null *and then* closes
  with the pre-M76 sentence: "That is a real limitation, surfaced as an
  explicit null rather than hidden." Leftover text asserting the opposite
  of the paragraph it ends.

Both are edit residue — a line updated, the sentence after it left
alone. Nothing catches that.

## The fix is a test, not a proofread

Proofreading fixes today's copy and nothing else; this file has drifted
under active maintenance the whole time. `tests/test_docs.py` scans
CLAUDE.md for `NAME = value` claims, keeps the ones naming a real
`api/config.py` constant, and asserts each matches — parametrized, so a
failure names the line and both values.

Three deliberate limits:

- **Only mechanically checkable claims.** Prose cannot be verified, and a
  test that tried would fail on rewording.
- **Unknown names are ignored, not failed.** The file also discusses
  engine names, response fields and pseudocode; policing vocabulary would
  be a nuisance and would get deleted.
- **A guard on the guard.** If the regex or the layout ever stops
  matching anything, every parametrized case passes vacuously and nobody
  notices — so one test asserts the scan finds *something*.

Verified by mutation: changing a value in CLAUDE.md produces
`CLAUDE.md:128 says MULTIWAY_BRANCH_TRAIN_ITERATIONS = 400, but
api/config.py has 100`. A doc test that has never been seen to fail is
not known to work.

The withdrawn-1.95x case gets its own named test, because a retracted
measurement reappearing is exactly what happens when someone summarises
the file.

## Also removed: the test counts

"750 backend tests / 145 frontend tests" in the verification block were
817 and 152 by the time anyone looked. They cannot be right for long and
nothing depends on them — the *command* is the useful part. Same for
"58 entries" describing a milestone log that has 69.

---

# Round 16 (M97) — building M74's prescribed fix, and refuting it

M74 closed by naming what the 6-max jam oscillation needs: "policy
damping (averaging the policy, or an optimistic-regret variant) — an
algorithmic change, not a parameter." Every round since has quoted that
as the way forward. This round built it, in both forms, and measured
both **worse than doing nothing**.

## The result

6-max, 3,000 iterations (the shipped budget), three seeds, a fresh
equity cache per run. Heads-up AA-jam reference ~0.031.

**Corrected in round 17:** these runs used the engine's default equity
sample count (200), not the API's shipped `MULTIWAY_PREFLOP_SAMPLES =
50` — every arm built `MultiwayEquityCache(hands=...)` without passing
`samples`. The arms stay comparable to each other, since all three made
the identical mistake, but "the shipped budget" below describes the
iteration count only.

| arm | AA jam | mean | spread | cost |
|---|---|---|---|---|
| **plain** | 0.036 / 0.073 / 0.058 | **0.056** | **0.037** | 262s |
| predictive | 0.562 / 0.963 / 0.359 | 0.628 | 0.604 | 258s |
| smoothing 0.9 | 0.141 / 0.172 / 0.732 | 0.348 | 0.591 | 275s |

Plain wins on level and on stability, at the same cost.

## Why — the part worth keeping

**Prediction is a full-information technique.** Under external sampling
the last instantaneous regret is dominated by the all-in: the noisiest
action in the tree, because its payoff swings a whole stack. That is the
same action M71 identified when it removed the CFR+ clamp. Adding
another copy of it amplifies sampling noise; it does not damp a cycle.

**Damping filters the wrong signal.** It lags regret matching's
*output*, while the oscillation lives in `regret_sum`, its *input*. A
delayed oscillation is still an oscillation and `strategy_sum`
accumulates it either way. The mechanism also has a hard ceiling —
enough damping to outlast a cycle thousands of iterations long also
stops the policy learning. Visible directly at `smoothing=0.99`: AA jams
**0.998 with a seed spread of 0.000** while T7s's fold *collapses* from
~0.94 to ~0.34. A perfectly stable wrong answer.

## A refinement of M74's diagnosis

At 12,000 iterations every arm sits at 0.40-0.61 mean, and damping
narrows the seed spread **without moving the level**. If that answer
were a cycle sampled at a random phase, damping should pull the mean
toward the cycle's average. It doesn't.

So the 12k answer is not "the right number, sampled at the wrong
moment" — the solver is converging somewhere genuinely wrong, and phase
is secondary. That redirects the next attempt from the policy dynamics
to **the question being asked**: the equity model's blocker-ignoring
pairwise approximation, and the pool. Which is precisely the shape of
M66/M67, where a "solver divergence" turned out to be a distorted
question the solver was answering correctly.

## Two methodology notes

**A confound found, checked, and correctly scoped.** The first two
scripts shared one `MultiwayEquityCache` across arms — intended to stop
any arm getting a colder start, but it aligned cache warmth with arm
order instead (a 3,000-iteration run went 283s to 1.4s across a
session). Re-running with a fresh cache per run returned **bit-identical
strategies**, because `MultiwayEquityCache` seeds each entry from
`(seed, opponent hands)` rather than a shared advancing RNG — a property
its own docstring states. The confound was real but **timing-only**. The
interim readings that predictive was faster and smoothing 2x slower were
artifacts; under equal conditions the three arms are within 7%.

**Two seeds is not a distribution, for the third time.** Smoothing's
first two seeds were 0.141 and 0.172 and looked tight; the third was
0.732 — and this write-up nearly reported "tight spread" off those two.
M73's exploration floor and M80's bias scare died the same way. It is
now a standing warning in `current_strategy`'s docstring rather than a
lesson each round relearns.

## What shipped

Both knobs stay, default off, like `discount` after DCFR measured worse
— a refuted approach that is still reproducible is worth more than one
that is only remembered. But `last_regret` and `last_strategy` are
stored **only when the flag that reads them is on**: they are two more
`(num_hands, num_actions)` arrays beside `regret_sum`/`strategy_sum`,
exactly **doubling** `node_data` — 8,500-9,300 tables on a 6-max solve,
the largest structure any solve produces, and the one M93 had just
finished bounding. A default-off feature pays nothing.

---

# Round 17 (M98) — the sizing defect is structural

M97 ended by saying the next attempt should look at *the question being
asked* rather than the policy. This round did, and found the answer in
one line of `cfr.py`.

## The root cause

Every showdown terminal is priced `equity * pot - invested`
(`_mccfr_terminal_value`). So:

| AA's line | model's payoff | reality |
|---|---|---|
| raise 2.5bb, called | `0.85 * 5.5 - 2.5` = **+2.2bb** | far more — a whole postflop game follows |
| jam 100bb, called | `0.85 * 200 - 100` = **+70bb** | correct — an all-in really does end at showdown |

**The all-in is the only action this tree prices correctly.** Every
smaller bet is scored as if the hand ended immediately, throwing away
the postflop value that is most of a raise's worth. The bias grows with
opponent count, because more opponents means more chance the
accurately-priced all-in gets called at all.

Heads-up does not show this, and **why is an open question.**

> **Corrected in round 18.** This paragraph originally claimed heads-up
> "escapes by cancellation, not soundness" — that a jam there just wins
> the 1.5bb blinds, worth less than a called raise even at the
> underpriced +2.2bb. That was a hypothesis written as a finding, and it
> does not survive scrutiny: a jam's value depends on villain's calling
> frequency **against the whole shoving range**, not on AA in isolation,
> and the equilibrium jam frequency is a property of the range's
> incentives rather than one hand's arithmetic.
>
> What this round actually measured is the pricing rule (read from
> `_mccfr_terminal_value`) and the 6-max convergence onto jamming. Why
> N=2 is unaffected is inferred, not shown — and a plausible causal story
> told confidently is the specific failure this document exists to catch.

## It is not a budget problem

The long-standing note said the sizing split was "not converged at this
budget", which implies budget would fix it. Measured at the most
converged, least noisy configuration tested — **12,000 iterations, 400
equity samples** — AA jams **0.649** and KK **0.709**. More iterations
and more samples converge *onto* the jam.

## Noise explains the instability, not the level

Measured directly against a 20,000-sample truth: a 50-sample multiway
equity estimate has error **sd 0.091**, which in a six-way 100bb pot is
**±55bb of EV** (worst observed 141bb). One opponent tuple's estimate
ranged **0.216–0.583** against a truth of 0.348. The cache freezes each
estimate per key, so CFR optimizes against its own noise rather than
averaging it out — which is the seed dependence the whole M72→M97 thread
chased. It is not what makes the answer jam-heavy.

## The warning was already in the repo

`equity.py`'s `MULTIWAY_DEFAULT_SAMPLES = 200` comment has said since
**M8** that at 50 samples equity noise gets *"amplified by the all-in
pot size into a large enough value error to visibly distort MCCFR's
learned strategy"*. `api/config.py` overrode it to 50 on a table
comparing `200 samples @ 300 iters` against `50 samples @ 3,000 iters` —
**two variables at once, one metric reported**. Every tuning decision on
that constant was measured on the fold axis. The sizing axis was written
off as unconverged rather than measured.

Same shape as R15's finding one layer down: a fact recorded in one file,
invisible where the decision was made.

## And I repeated the error

M97 claimed its arms were measured "at the SHIPPED operating point".
They were not — every script built `MultiwayEquityCache(hands=...)`
without `samples`, silently taking the engine default of 200 where the
API ships 50. The arm-vs-arm conclusion survives (all three made the
identical mistake), but the milestone that *quotes* the rule about
validating at the shipped operating point did not follow it. Corrected
in four places rather than left standing.

## What shipped

A multiway preflop solve answers two questions and is only good at one.
One confidence number could not say that, so a 6-max player asking
*"raise or shove?"* received the same `solver_confidence: "high"` as one
asking *"play or fold?"*.

`sizing_confidence` is deliberately **separate** from
`solver_confidence`: marking the whole response low would understate the
fold-vs-play call, which is the converged part and what most players are
actually asking. Scoped to preflop, rendered in the UI, and tested for
what it does *not* do as much as what it does — it must not fire
heads-up, must not fire postflop, and must not hide behind 9-max's
existing warning.

**Not attempted:** postflop continuation value at preflop terminals,
which is what a real fix needs. That is an architectural change to
terminal pricing and deserves its own milestone, not a bolt-on to a
diagnosis.

---

# Round 18 (M99) — the constant was right; the reasoning wasn't

R17 found the multiway sizing defect and implicated
`MULTIWAY_PREFLOP_SAMPLES = 50` in it. This round set out to change that
constant and found it should stay.

## Iterations dominate, decisively

The constant's original evidence compared `200 samples @ 300 iters`
(~170s) against `50 @ 3,000` (325s) — two variables at once, two
different costs, one metric. This held wall clock roughly fixed, 9 seeds
per arm, reading T7s's under-the-gun fold rate:

| arm | T7s fold ± SE | worst | below 0.80 | AA jam | cost |
|---|---|---|---|---|---|
| **50 × 3,000** | **0.866 ± 0.051** | 0.486 | **2/9** | 0.116 | **98s** |
| 200 × 750 | 0.485 ± 0.099 | 0.061 | 8/9 | 0.832 | 144s |
| 400 × 375 | 0.419 ± 0.111 | **0.000** | 7/9 | 0.991 | 149s |

Starve the iterations and one seed in nine folds T7s **0% of the time**
under the gun while jamming AA 99%. The shipped arm wins on every
measure and is also the cheapest.

Note the arms are **not** equal-cost despite holding `samples ×
iterations` constant — per-iteration tree and NumPy work doesn't scale
with samples, so the richer-sample arms cost ~47% more. Holding the
product constant is not holding cost constant.

A separate sweep at fixed iterations shows what samples *do* buy:
stability. Going 50 → 200 → 400 at 3,000 iterations, AA-jam SE falls
0.066 → 0.098 → 0.021 and its range narrows 0.013–0.635 → 0.015–0.204,
while T7s's fold rises 0.866 → 0.924 → 0.942. At fixed **cost**, that
stability is not worth the iterations it costs.

**What none of it fixes:** at the shipped setting, 2 seeds in 9 still
fold T7s below 0.80, one at 0.486. That is what R17's ±55bb frozen
equity error buys, and no retuning removes it.

## A metric that hid the defect it was built to find

The equal-cost script averaged three hands that should fold. J4o and 95o
fold ~0.999 in every arm, so for the **same nine solves** the mean
reported **0.955, worst 0.829, 0/9 below 0.80** while T7s alone showed
**0.866, worst 0.486, 2/9**.

Same runs. Two metrics. Opposite verdicts. Diluting a metric with cases
that cannot discriminate doesn't merely lose power — it conceals. I
added the extra hands specifically to make the metric more robust.

## Four corrections to R17

Separating what was measured from what was inferred:

1. **"Heads-up escapes by cancellation" is retracted.** It was a
   hypothesis written as a finding, and the arithmetic doesn't hold — a
   jam's value depends on villain's calling frequency against the
   **whole** shoving range, not one hand's. Why N=2 is unaffected is
   open.
2. **The preflop scoping of `sizing_confidence` is flagged.** R17
   justified it with "postflop carries its own trained/range_confidence
   signals" — which describes the *range* and says nothing about
   terminal pricing.
3. **A test claimed exact solving repairs a pricing defect.** Heads-up
   preflop has the same three unmodelled streets; it now pins behaviour
   and states the soundness is unmeasured — and unmeasurable in-repo,
   since no deeper preflop tree exists to compare against.
4. **A stale comment** in `_advise` still described M84 routing through
   `solve_flop_turn` with a shared cache; M88 replaced that eleven
   milestones earlier.

## The flop question, and a fixture rejected

Does the pricing flaw reach the flop? `solve_flop` **is** flop-only, so
the prediction is concrete. The first attempt could not answer it: with
the 2-class demo ranges at SPR 9.5 the solution is 0.9999 pure, and
adding a real turn moved the root strategy by 2.9e-04. A degenerate
fixture cannot detect an effect whether or not it exists — the same
mistake shape as the diluted metric above, twice in one round.

The replacement widened both ranges (value / marginal / draw / air) and
dropped SPR to 1.5 so the all-in genuinely competes, and reported a
mixedness number so a real null could be told apart from another dead
fixture. It answered cleanly:

| tree | all-in | check | mixedness | nodes | time |
|---|---|---|---|---|---|
| flop only | **0.5652** | 0.4348 | 0.687 | 4 | 3.1s |
| + real turn | **0.5099** | 0.4901 | 0.601 | 200 | 26.5s |
| + turn and river | **0.4635** | 0.5365 | 0.592 | 9,608 | 665.8s |

**The flaw reaches the flop.** Each street of future betting the tree
gains moves ~5 percentage points off the all-in — 10.2pp monotone from
flop-only to fully chained. The exact two-player solver is deterministic,
so this is a real difference rather than noise, and the *monotonicity* is
what a coincidence would not produce.

Two honest limits. The low SPR means a 2.5x-pot raise exceeds the stack
and collapses into the all-in, so this measures weight moving between
all-in and CHECK, not all-in and raise. And it is one spot.

It is deliberately **not** surfaced as a caveat at the flop: 5.5pp is an
order of magnitude below the preflop distortion, and a warning on every
postflop response would devalue the preflop one that marks a genuinely
unusable axis.

---

# Round 19 (M100) — testing the architectural fix cheaply, and not
# validating it

R17 named postflop continuation value at preflop terminals as the real
fix for the sizing defect. That is expensive architecture — chaining a
flop off every preflop terminal — so this round asked the cheap question
first: is the diagnosis **sufficient**, or merely consistent?

`_mccfr_terminal_value` gained a `continuation` coefficient. Where chips
remain behind, a hand's payoff gains `c * (equity - 1/n_live) *
chips_behind` — a crude stand-in for the game the tree cannot see. Two
properties keep it honest, both pinned by tests: it touches only
terminals with money behind, so an all-in is untouched (that asymmetry
**is** the defect), and it is exactly zero-sum at equal stacks, so it
cannot fake an improvement by paying everyone.

## Three designs, one answer

**Sweep (4 coefficients x 3 seeds).** AA's all-in frequency:

| budget | c=0 | c=0.25 | c=0.5 | c=1.0 |
|---|---|---|---|---|
| 12,000 | 0.615 | 0.208 | 0.417 | 0.374 |
| 3,000 | 0.061 | 0.112 | 0.287 | 0.010 |

Non-monotone at both budgets. A term capturing a real mechanism moves the
number one way as it is turned up; this goes down, up, down.

**Paired (2 arms x 9 seeds).** Both arms on the same seed, so the
per-seed difference cancels seed variance rather than averaging it away:

| | AA jam ± SE | range |
|---|---|---|
| c=0 | 0.494 ± 0.068 | 0.258–0.856 |
| c=0.25 | 0.434 ± 0.090 | 0.065–0.820 |

**Paired delta −0.060 ± 0.137, fell in 5 of 9.** A coin flip.

## The two traps

**`c=1.0 @ 3,000` looks like the fix and is not.** It gives 0.010 ±
0.005, an order of magnitude tighter than any other arm. But a large
bonus for keeping chips behind makes the all-in *dominated*, so the
policy goes purely "never jam" and lands **below** the ~0.031 reference.
That is hitting the target by making the action unattractive, not by
modelling what follows it. **The knob can produce any number, so matching
the reference does not validate it.**

**A paired design's interim is no safer than any other interim.** At 4
pairs this read 4/4 falling, mean −0.31, and looked real. Seeds 5–9
erased it. Pairing removes seed *variance*, not the need for the full
sample. Three times in this one round a partial result pointed the wrong
way: a single seed read as an arm, a 3-seed cell read as a fix, and 4/4
read as a trend.

## What this does and does not settle

It does **not** refute R17. The pricing asymmetry is read straight from
the code, and R18 confirmed it postflop with a monotone 10.2pp effect.
What it settles is that a *linear edge-times-stack stand-in* is not a
valid substitute for solved continuation values — so the architectural
work cannot be justified, costed, or designed on this evidence, and still
needs its own milestone.

`continuation` stays, default 0.0, like `optimism`/`smoothing` after R16:
a refuted approach that stays reproducible beats one that is only
remembered. Unlike those it costs no memory — arithmetic, not an array.
