# Maximal diagnostic — 2026-08-24 (M124)

A whole-project pass: static structure, mechanically re-verified
constraints, live behaviour at production settings, latency normalized
against a same-run reference, and the product surface.

Run after audit rounds 1–15 (M101–M123), which had already fixed six
seam defects and one engine defect. This one asks what is left.

**Headline: the engine is in good shape and the product's worst problem
is latency, not correctness.** Every correctness probe came back clean.
The single largest finding is that a cold multiway preflop request costs
**66–93 seconds**, and the cache key makes that unavoidable for all but
three stack depths.

---

## Method

Timings are normalized against a fixed reference workload measured in
the **same process**, because this machine drifts ~1.7× between sessions
(M70) and absolute wall-clock across sessions is not comparable. The
reference is a 300-iteration heads-up preflop solve; `ref units` below
are multiples of it.

---

## 1. Latency — the product's real weak point

| request | cold | ref units | warm | warm speedup |
|---|---|---|---|---|
| preflop heads-up | 3.42s | 3.5 | 2.4ms | 1,415× |
| **preflop 6-max** | **66.3s** | **67.3** | 2.2ms | 29,581× |
| **preflop 9-max** | **92.8s** | **94.1** | 22ms | 4,193× |
| flop heads-up | 6.51s | 6.6 | 4.5ms | 1,459× |
| flop mid-street | 6.48s | 6.6 | 4.0ms | 1,627× |
| turn heads-up | 18.3s | 18.6 | 3.2ms | 5,757× |
| river heads-up | 11.7s | 11.9 | 11.6ms | 1,005× |
| flop 3-max | 25.8s | 26.2 | 4.0ms | 6,494× |

Warm performance is excellent everywhere — every endpoint answers in
single-digit milliseconds once cached, at 1,000–29,000× the cold cost.
The caching architecture works.

The problem is exclusively **cold multiway preflop**.

### D1 — the multiway preflop cache is keyed at 1bb granularity *(high)*

`_get_or_solve_multiway` keys on `(round(stack_bb), players)`. The solve
behind that key costs 66–93s measured here (its own docstring says
75–215s). The startup pre-warm covers **three depths** — 100, 50 and
20bb — across three table sizes, so nine entries.

Every other depth is a cold 66–93s wait, and they are all distinct: a
client asking about 75bb, then 76bb, then 77bb pays it three times.
Heads-up pre-warms seven depths (20/40/50/75/100/150/200) and costs only
3.4s cold anyway, so the exposure is specific to multiway.

The project already solves exactly this problem one layer over.
`canonicalize.canonical_stack_depth` buckets postflop library lookups
into 5bb bands and **floors** rather than rounding — deliberately, so
that `canonical <= real` holds and no derived bet size can be
unaffordable (F13, fixed M95). The preflop multiway cache does not use
it.

Bucketing here is **not** a free copy of that decision, and this
diagnostic does not assume it is. Preflop strategy at short depths is
genuinely depth-sensitive in a way postflop board texture is not — a
24bb spot solved at 20bb is a materially different push/fold problem,
while 99bb solved at 95bb is nearly the same game. The recommendation is
therefore to **measure the substitution error per depth band and adopt
bucketing only where it is small**, not to bucket uniformly.

---

## 2. Correctness — no defects found

### Live play, production settings

40 random deals across table sizes 2/3/6, stack depths 20/50/100bb, and
three preflop action shapes: **40 answered, zero malformed responses,
zero strategy rows that were not probability distributions, zero
categorical violations** (no premium folded, no trash never folding).

### Documented defects still behave as documented

| claim | holds |
|---|---|
| 9-max marked `solver_confidence: low` (M76) | yes |
| 6-max carries the sizing caveat (M98) | yes |
| no advice names an unaffordable bet (M101) | yes — swept, none |
| cheap validation runs before expensive solves (M101) | yes — bad request rejected in **2ms** |

That last one is worth stating plainly: M101 fixed a path that took
**76.2 seconds to return a 422**. It now returns in 0.002s, and the fix
has held through twenty-plus milestones.

### CLAUDE.md constraints, mechanically re-verified

Eight of nine checkable constraints verified directly against the code:

- every solve cache bounded (M93/M104) — see D2
- `_SolveCache.lock` is a genuine `RLock` (M104)
- every `*Request` model forbids extra fields (M102)
- the engine imports no web framework (enforced boundary)
- `canonical_stack_depth` floors, never rounds up (M95) — checked at six depths
- every refuted knob still defaults off — `floor_regret=False`,
  `optimism=0.0`, `smoothing=0.0`, `continuation=0.0`,
  `continuation_table=None` (M71/M97/M100/M115)
- no raise is ever offered after an all-in (M117)
- a class's frequency is carried per-combo, whole deck uniform (M119)

---

## 3. Structure

### Coverage

**95% overall** — 2,785 statements, 131 missed. Eleven of eighteen
engine modules are at 100%.

| module | cover |
|---|---|
| `api/main.py` | 83% |
| `poker_solver/cfr.py` | 92% |
| `api/solving.py` | 94% |
| `poker_solver/abstraction.py` | 96% |
| `library.py`, `continuation.py`, `multiway_board_equity.py` | 97% |
| `solver.py`, `game_tree.py`, `combos.py` | 98% |
| `api/caches.py`, `equity.py` | 99% |
| `chance.py`, `hand_eval.py`, `cards.py`, `canonicalize.py`, `board_equity.py`, `strategy_format.py`, `starting_hands.py`, `hand_utils.py`, `api/config.py`, `api/schemas.py` | 100% |

The two largest gaps are both explicable and one of them is D2:
`api/main.py`'s missing block is almost entirely the pre-warm (disabled
in tests on purpose), and `cfr.py`'s is the M115 continuation-table
branch, which is refuted and default-off but kept for reproducibility.
The remainder are scattered exception handlers.

### Healthy

- **Zero** TODO/FIXME/XXX/HACK across `poker_solver/`, `api/` and `tests/`
- **Zero** bare `except:` and **zero** bare `assert` in production code
- No dead code: all eight functions never named in a test are live,
  called from production and covered indirectly
- Source-to-test line ratios above 0.9 for every engine module bar three
  small ones; `solver.py` 1.59×, `game_tree.py` 1.60×, `chance.py` 1.63×
- Frontend: `oxlint` clean, `tsc --noEmit` clean, builds in 164ms,
  220KB bundle (67.7KB gzipped), and **every one of 13 components has a
  colocated test**
- 16 routes, 5 of them deprecated but functional, all documented

### D2 — the pre-warm is untested, uncovered, and fails silently *(high)*

`_prewarm_common_depths` is the **only** thing standing between users and
D1's 66–93 second cold wait. It is also:

- **0% covered.** Lines 687–768 of `api/main.py` are the single largest
  uncovered block in the project, and the only test that mentions the
  pre-warm at all is the autouse fixture that *disables* it.
- **Silent on failure.** Every warm is wrapped in
  `except Exception: logger.exception(...)`. A config typo, a renamed
  helper, an exception in one branch — none of it fails anything.
- **Fire-and-forget.** It runs in a daemon thread started at import;
  nothing ever observes whether it finished, or finished successfully.

If it broke tomorrow, the symptom would not look like a bug. It would
look like "the product is slow" — every multiway user paying 66–93s
forever, with a stack trace sitting in a log nobody reads. This is the
same failure shape as F25 (M107: nothing verified the app was served at
all), and it is worse here because the thing it silently stops doing is
the mitigation for the largest performance problem in the product.

### D3 — the one unbounded cache's exemption is asserted, not enforced *(medium)*

`test_no_solve_cache_is_unbounded` exempts `multiway_equity` by name,
justified in the test itself as "keyed by the HAND POOL, which is a
config constant rather than anything a request supplies, so its key
space is effectively one entry".

Traced, and the justification is **currently true**:
`_get_multiway_equity_cache` has exactly one caller, passing
`cfg.MULTIWAY_PREFLOP_HANDS`. But nothing checks that. If a caller ever
passes a request-derived pool, the exemption becomes false silently and
the test keeps passing.

This is the F27 shape M104 already fixed twice: a cache justified by
what fills it today rather than by what its key admits. The exemption
should verify its own premise.

### D4 — coverage measures execution, not assertion *(medium)*

Line coverage is **95% overall** (2,785 statements, 131 missed), and
`api/solving.py` specifically is at **94%**. So the fact that 20 of its
31 module-level functions are never *named* in a test is not a coverage
gap — they run on essentially every HTTP request.

That is exactly what makes the finding worth stating. `_cap_range_to_
combos` was covered — it executed on every river request — and it still
shipped the defect M119 found, collapsing a river range onto nine ways
to hold J3o. Coverage proved the line ran. Nothing asserted what it
returned.

The remedy is therefore *not* "raise coverage". It is to assert
**properties** of the helpers that carry real logic — the range caps,
street inference, path validation — because an end-to-end HTTP
assertion cannot see that a range degenerated on the way through.

### D5 — one constant with no stated justification *(low)*

`MAX_ITERATIONS = 20_000` sits with no comment of its own. CLAUDE.md
says every constant in `api/config.py` carries its measured
justification; 46 of 48 do, the two known gaps are already documented as
gaps (M101/M110), and this is the third.

A first pass flagged 25 constants and **that was wrong** — the heuristic
misread shared comment blocks covering several constants and
`= DEFAULT_X` aliases that inherit a measured value. Checked before
reporting; the real number is one.

---

## Recommendations, in priority order

1. **D1 — measure floor-bucketing the multiway preflop cache key, adopt
   where the error is small.** Highest user-visible impact: it is the
   difference between a 66-second wait and an instant answer for every
   stack depth outside the pre-warmed three. Must be measured per depth
   band rather than applied uniformly, because preflop is depth-
   sensitive where postflop board texture is not. Floor, never round —
   the M95 constraint applies here for the same reason it applies there.
2. **D2 — make the pre-warm observable and test it.** It is the sole
   mitigation for D1 and it currently cannot fail loudly. At minimum:
   a test that it actually warms what it claims to, and a way for an
   operator to see whether it succeeded.
3. **D3 — make the unbounded-cache exemption verify its own premise**,
   so a future caller passing a request-derived pool fails loudly.
4. **D4 — assert properties of the `api/solving.py` helpers that carry
   real logic**, starting with the range caps that already produced a
   defect at full line coverage.
5. **D5 — give `MAX_ITERATIONS` a justification** or record it as a
   known gap alongside the other two.

---

# Acted on — all five, in priority order (M124)

## D1 — the multiway preflop cache now buckets *(fixed)*

Keyed on a 5bb **floor** bucket instead of `round(stack_bb)`, and the
solve runs at the bucketed depth rather than the requested one.

Solving at the bucketed depth is the load-bearing half. Keying on the
bucket while solving at the real depth would serve the first caller's
deeper tree to everyone in the band, and a tree built at 99bb offers
bets a 95bb player cannot make — F13 exactly, which M95 fixed for the
postflop library by flooring. Flooring keeps `canonical <= real`, so
every derived size stays affordable by construction.

**This was adopted on a control, not on analogy.** Preflop is genuinely
depth-sensitive where postflop board texture is not, so the question was
never "is the substitution error zero" but "is it small next to the
noise already there". Measured at 3-max over all 169 classes' fold
frequency, against the control of re-running the *same depth* under a
different seed:

| depth | same depth, seed 1 vs 2 | 5bb floor-bucket, same seed |
|---|---|---|
| 24bb | mean .050, max .778, 8 flips | mean .051, max .894, 8 flips |
| 99bb | mean .053, max .652, 12 flips | mean .046, max .453, **10 flips** |

Bucketing 4bb away moves the strategy no more than re-running the
identical solve does — less, at 99bb. Without that control the
bucket-vs-truth numbers look alarming and would have been reported as
depth sensitivity; the same over-read M110 made and M111 corrected.

Measured through the real API afterwards:

| request | before | after |
|---|---|---|
| 97bb (cold) | 28.3s | 28.3s |
| 98bb | 28.3s | **0.0028s** |
| 99bb | 28.3s | **0.0024s** |
| 92bb (next band) | 20.7s | 20.7s |

Three consecutive depths that cost ~85s now cost one solve.
**Affordability swept at 97/98/99/23/24/6bb: zero violations.**

The control also says something uncomfortable in its own right, recorded
rather than buried: **8–12 of 169 hands cross the fold/play line between
two runs differing only in seed.** That is the multiway instability
M73/M74 and M111 already document, seen from a new angle — the 66s a
user waits buys an answer that would partly differ if run again.

## D2 — the pre-warm is observable and tested *(fixed)*

Seven duplicated `except Exception: logger.exception(...)` blocks
collapsed into one `_prewarm_step` helper that records every outcome in
`PREWARM_STATUS`. Failures still do not abort the run — one unavailable
spot must not cost every other warm, which is why the original swallowed
them — but they are now recorded with their cause instead of vanishing
into a log, and the run reports how many steps failed.

Two tests: one that the pre-warm attempts everything config names,
records success, and genuinely populates the cache; one that a failing
step is recorded *with its error* and does not stop later warms.
Mutation-checked — emptying the depth list fails both, and dropping the
error string fails the second.

## D3 — the unbounded-cache exemption verifies its own premise *(fixed)*

`test_no_solve_cache_is_unbounded` exempted `multiway_equity` on the
grounds that its key is a config constant. That was true and unchecked.
The test now inspects every call site of `_get_multiway_equity_cache`
and fails unless each passes a `cfg.` constant. Mutation-checked:
pointing one call at a request-derived pool fails the test.

## D4 — properties asserted where coverage could not help *(fixed)*

Nine direct tests for the helpers that carry real logic: `_cap_range`
selecting by frequency and staying stable on ties, `_infer_street`'s
full mapping, and its contradiction guards for skipped streets.

Worth restating why this was not a coverage problem. Those functions sat
at ~94% line coverage before a single one of these tests existed —
`_cap_range_to_combos` ran on *every* river request and still shipped
M119's defect. Coverage proves a line ran. Only an assertion proves what
it returned.

## D5 — `MAX_ITERATIONS` has its measurement *(fixed)*

1,000 iters → 2.8s, 5,000 → 12.1s, 20,000 → 50.0s for a 169-class
heads-up solve. The ceiling is a ~50s worst case, the same bracket
`MAX_FLOP_TURN_ITERATIONS` and `MAX_FLOP_MULTIWAY_ITERATIONS` were set
against. It bounds a client's request, it does not mark a convergence
limit — heads-up CFR+ is converged long before it.
