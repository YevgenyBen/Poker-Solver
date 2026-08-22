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
