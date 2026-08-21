# Whole-project audit: code quality, redundancy, and speed (2026-08-21)

A full scan of the codebase as it stands after M56, covering both
**structural quality/redundancy** and **measured speed**. Requested
after the `/advise` frontend landed, as a checkpoint before deciding
what to build next.

Method: direct measurement and code reading, not estimation. Every
number below came from running the real code through the real FastAPI
stack (`TestClient`) at **production settings** — no monkeypatched caps,
no shrunken demo pools. Structural claims were verified by executing
them (e.g. the duplicate-solve finding in §2.1 was confirmed by
inspecting both caches after real requests), not inferred from reading.

Nothing was fixed as part of this document. It exists to inform
prioritization, mirroring `full-table-diagnostic-2026-08.md`'s own role
before M27-M34.

**Headline:** the codebase is in good structural health — **zero dead
public functions, 906 passing tests, a clean lint/type pass, and an
engine/API boundary enforced by test**. The findings below are about
*accumulated surface area*, not defects. The single most actionable item
is small and verified: **the same preflop spot is solved twice into two
separate caches** (§2.1), wasting ~3.2s on the most common first
interaction a user has with the app.

## Table of contents

1. [Scope and method](#1-scope-and-method)
2. [Redundancy findings, ranked](#2-redundancy-findings-ranked)
3. [Structural / maintainability findings](#3-structural--maintainability-findings)
4. [What's healthy](#4-whats-healthy)
5. [Performance benchmarks](#5-performance-benchmarks)
6. [Notable benchmark findings](#6-notable-benchmark-findings)
7. [Prioritized recommendations](#7-prioritized-recommendations)

---

## 1. Scope and method

| | |
|---|---|
| Code scanned | `poker_solver/` (11 modules), `api/` (2 modules), `frontend/src/` (8 components + hooks/api/types) |
| Total source | ~16,100 lines (excl. `node_modules`, `__pycache__`) |
| Tests | 742 backend (pytest) + 164 frontend (vitest) = **906** |
| Benchmarks | 24 endpoint measurements, production settings, single run each unless noted |
| Machine | Same host throughout; absolute numbers are machine-specific, ratios are the durable part |

**A caveat stated up front:** benchmark figures are single measurements
unless marked otherwise. This session has repeatedly found single
readings misleading (M49 and M54 both caught bad numbers by re-running),
so treat these as *order-of-magnitude and ranking* evidence, not
precision timings. Where a number drives a recommendation below, that's
called out.

---

## 2. Redundancy findings, ranked

### 2.1 The same preflop spot is solved twice into two separate caches — VERIFIED

`GET /solve/{stack_bb}` caches a **formatted response** in `_cache`.
Everything path-derived (`/advise`, `/preflop_walk`, all five
`*_from_path` endpoints) caches a **raw `StrategyResult`** in
`_preflop_raw_cache`. At `players == 2` these are two independent
`solve_preflop()` calls for the identical spot.

Verified directly:

```
after GET /solve/100     -> _cache: 1 | _preflop_raw_cache: 0
after POST /preflop_walk -> _cache: 1 | _preflop_raw_cache: 1
```

**Cost:** ~3.2s of duplicated work (the measured cold heads-up preflop
solve), paid on exactly the most likely first user journey — open the
Preflop Ranges tab, then use the Advisor.

Note this is already solved for `players != 2`: M29 made
`_get_or_solve_preflop_raw` delegate to `_get_or_solve_multiway`,
sharing one cache. The heads-up path just never got the same treatment.
**Smallest, best-understood win in this document.**

### 2.2 The API surface has ~6 endpoints `/advise` now supersedes

16 routes exist. `/advise` (M51-M53) was built as the unified front door
and now covers every (street × table size) cell, which makes these
functionally redundant for new callers:

| Route | Superseded by |
|---|---|
| `/solve_flop_from_path` | `/advise` (flop, heads-up) |
| `/solve_flop_multiway_from_path` | `/advise` (flop, multiway) |
| `/solve_turn_from_path` | `/advise` (turn, heads-up) |
| `/solve_turn_multiway_from_path` | `/advise` (turn, multiway) |
| `/solve_river_from_path` | `/advise` (river, heads-up) |

They aren't dead — `/advise` *delegates* to their orchestrators, and the
frontend's older tabs still call them — but as **public API surface**
they're now redundant with a strictly more capable endpoint. Each also
carries its own request/response schema (`api/schemas.py` is 457 lines,
much of it near-duplicate response models).

### 2.3 Frontend: the same combo-row markup appears 11 times across 8 components

Every results-rendering component hand-rolls the identical
`detail-row → label → bar-track/bar-fill → breakdown → trained-indicator`
block:

```
ActionPathSolver.tsx     2    FlopSolver.tsx           1
AdviseSolver.tsx         2    MultiwayFlopSolver.tsx   1
TurnPathSolver.tsx       2    CachedFlopSolver.tsx     1
DetailPanel.tsx          1    EquityCalculator.tsx     1
```

A single `<ComboRow combo freqs trained />` would replace all 11. This
is the clearest pure-duplication finding in the frontend, and the
cheapest to fix.

### 2.4 Frontend: 8 tabs, 7 of them narrower tools the Advisor now covers

`Advisor` (M56) answers what `Flop Solver`, `Cached Flop Solver`,
`Multiway Flop Solver`, `Action-Path Wizard`, and `Turn Advisor` each
answer a slice of. `Preflop Ranges` (the 169-cell grid) and `Equity
Calculator` remain genuinely distinct.

Not necessarily a problem — they're useful for isolating one behavior
during development, which was their stated purpose. But five tabs is a
lot of surface to keep working, and each has its own test file.

---

## 3. Structural / maintainability findings

### 3.1 `api/main.py` is 3,441 lines — 2.3x the next largest file

| File | Lines |
|---|---|
| **`api/main.py`** | **3,441** |
| `poker_solver/solver.py` | 1,480 |
| `poker_solver/cfr.py` | 938 |
| `poker_solver/equity.py` | 614 |
| `poker_solver/game_tree.py` | 586 |

Composition: 46 functions, 16 routes, 28 module-level cache/lock
globals, and a **531-line module docstring** (15% of the file; comments
overall are 17%).

The documentation density is a deliberate and, on balance, valuable
project convention — it's why measured findings survive across
milestones. But the *file* has become the place everything lives:
constants, caches, orchestrators, routes, and pre-warm logic. Natural
split lines already exist (`constants` / `caches` / `orchestrators` /
`routes`).

### 3.2 28 cache/lock globals with no registry

Each endpoint owns its own `_x_cache` dict and `_x_lock`. That
separation is deliberate and well-justified (a shared dict could collide
across endpoints with different `max_raises` — documented repeatedly).

The maintenance cost is real though: `tests/test_api.py`'s autouse
fixture clears **13 caches by hand**, twice (setup and teardown), and
every new endpoint has to remember to add itself to both lists. A
registry (`ALL_CACHES = [...]`, cleared by iteration) would make that
impossible to forget. This has already been a live bug source — each
new endpoint milestone had to patch both lists.

### 3.3 `CLAUDE.md` is 4,297 lines across 49 milestone entries

It's the project's living reference and its detail is genuinely useful.
But it's now long enough that finding a specific decision means grepping
rather than reading. The per-milestone changelog format also means
superseded findings sit alongside current ones — e.g. M54's profiling
claim was wrong and M55 corrected it, but both entries remain, and only
the M55 entry says which is right.

Worth considering: a short "current state" section at the top
(architecture, key constants, known gaps) with the milestone log kept
below as history.

---

## 4. What's healthy

Worth recording explicitly, because an audit that only lists problems
misrepresents the codebase:

- **Zero dead public functions.** A scan of every non-underscore
  function in `poker_solver/` found all of them referenced somewhere in
  the engine, API, or tests.
- **906 tests**, all passing, with real correctness signal rather than
  smoke tests — including exhaustive checks where feasible (M48's
  6,188-multiset hand-evaluator cross-validation, M19's 22,100-flop
  canonicalization enumeration).
- **The engine/API boundary is enforced by test**, not convention —
  `test_package_boundary.py` fails the build if `poker_solver/` ever
  imports FastAPI.
- **Clean `oxlint` and `tsc --noEmit`.**
- **Approximations are documented where they're made**, with measured
  bounds, rather than hidden.

---

## 5. Performance benchmarks

All at production settings, via the real FastAPI stack. `~0.00s` means
sub-10ms (a cache hit).

### Preflop and support

| Endpoint | Time |
|---|---|
| `GET /solve/100` (heads-up, cold) | 3.15s |
| `GET /solve/100` (cached) | ~0.00s |
| `GET /solve/100?players=3` (cold) | **23.92s** |
| `GET /solve/100?players=6` (cold) | 11.33s |
| `GET /solve/100?players=9` (cold) | 25.16s |
| `GET /equity` (AA vs KK on a flop) | ~0.00s |
| `POST /preflop_walk` (root) | 3.55s |

### `/advise` — heads-up

| Query | Time |
|---|---|
| preflop (preflop leg warm) | ~0.00s |
| flop — library **miss** | 5.57s |
| flop — library **hit** | ~0.00s |
| turn — exact, cold | 10.78s |
| turn — cached solve, different card | ~0.00s |
| river — exact, cold | **16.65s** |

### `/advise` — multiway (3-max)

| Query | Time |
|---|---|
| flop, cold | 14.23s |
| turn, cold | 2.22s |
| river, cold | 1.96s |

### Legacy demo endpoints

| Endpoint | Time |
|---|---|
| `GET /solve_flop` | 1.16s |
| `GET /solve_flop_turn` | 8.18s |
| `GET /solve_flop_to_river` | 17.47s |
| `GET /solve_flop_multiway` | 0.96s |
| `GET /solve_flop_cached` (miss) | 0.80s |
| `GET /solve_flop_cached` (hit) | ~0.00s |

---

## 6. Notable benchmark findings

### 6.1 3-max preflop is the slowest table size — slower than 6-max, near 9-max

23.92s (3-max) vs 11.33s (6-max) vs 25.16s (9-max). Not a bug: 3-max
runs **100,000** MCCFR iterations while 6-max and 9-max run **300**
(`MULTIWAY_TABLE_CONFIGS`). M27 cut 6-max from 30,000 to 300 after
finding a convergence instability, and 9-max was always conservative.

The inversion is worth naming because it means **6-max and 9-max are
getting ~333x less solving than 3-max**, and 6-max at 300 iterations
costs only 11s — there is real headroom. M27's instability finding is
the blocker, not cost, and that finding predates M48's and M55's
speedups.

### 6.2 Multiway postflop is *faster* than heads-up — because its preflop pool is tiny

Turn: 2.22s multiway vs 10.78s heads-up. River: 1.96s vs 16.65s.

Counter-intuitive, and the cause is structural rather than algorithmic:
at `players != 2` the preflop leg solves over `DEMO_MULTIWAY_HANDS`' **8
classes**, so derived ranges are far narrower than the heads-up path's
169-class derivation. The multiway numbers are fast because they're
answering an easier question, not because multiway solving is cheaper.
**This is a fidelity gap wearing a speed win's clothing** — worth
remembering before treating multiway advice as equally trustworthy.

### 6.3 Cache hits are uniformly free; cold costs are the whole story

Every hit measured sub-10ms — the library (M21), the per-endpoint solve
caches, and the preflop caches all deliver. Optimization effort belongs
entirely on cold paths.

### 6.4 The slowest real path is 16.65s, down from ~46s pre-M48

River heads-up cold. For context on trajectory: turn heads-up advice was
~46s before M48, and is 10.78s here — M48 (hand evaluator) and M55
(equity-table memoization) together, plus M54/M55's cap raises, which
means the current figure is at *higher* range fidelity than the old one.

---

## 7. Prioritized recommendations

Ranked by (value ÷ effort), with the reasoning that ranks them.

### #1 — Unify the two preflop caches (§2.1)
**Small, verified, immediate.** Make `_get_or_solve` and
`_get_or_solve_preflop_raw` share one solve, mirroring what M29 already
did for `players != 2`. Saves ~3.2s on the most common first user
journey. Lowest risk item here: the mechanism already exists and is
tested.

### #2 — Extract a `<ComboRow>` frontend component (§2.3)
Replaces 11 hand-rolled copies across 8 files. Pure deduplication, no
behavior change, protected by 164 existing tests. Makes any future
change to how a strategy row looks a one-place edit.

### #3 — Add a cache registry (§3.2)
Replace 28 ad-hoc globals with one registry the test fixture iterates.
Removes a recurring per-milestone footgun (every new endpoint has had to
remember two manual clear-lists).

### #4 — Split `api/main.py` (§3.1)
3,441 lines with natural seams already present. Moderate effort, real
long-term payoff, but genuinely riskier than #1-#3 — it touches every
endpoint at once. Worth doing *after* the cheaper wins, and with the
same "existing tests unchanged" proof M50 used.

### #5 — Revisit 6-max / 9-max preflop iteration budgets (§6.1)
They sit at 300 iterations because of M27's convergence instability
finding — which predates two major speedups. Re-testing whether higher
counts are now both affordable *and* stable is a real fidelity
opportunity. **Needs measurement first**, and M27's warning is that more
iterations made things *worse*, so this is investigation, not a dial to
turn.

### #6 — Decide the fate of the superseded endpoints and tabs (§2.2, §2.4)
Not urgent, and there's a real argument for keeping them (isolating one
behavior during development). But a decision recorded is better than
drift. Options: keep as-is and document them as development tools;
deprecate the routes while keeping the orchestrators; or remove the
tabs while keeping the routes.

### #7 — Restructure `CLAUDE.md` (§3.3)
Add a "current state" summary above the milestone log. Lowest urgency,
but the file is approaching the size where its own usefulness degrades.

---

**Not recommended:** the two-phase solve (M47's other named lever). M55
found the real bottleneck was equity-table construction, fixed it
losslessly for a 3-4x gain, and left `_solve_recurse`'s own self time at
roughly 12% of the total. A speculative architectural change that
changes solver output, for a remaining ~12% slice, is a bad trade at
this point.
