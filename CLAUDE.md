# Poker Solver

## Current state (read this first)

A Texas Hold'em GTO solver engine plus a web UI for exploring it. The
engine is the product; the frontend is a tool for driving it.

### What it does today

Given **your hole cards, the board, your position, and the action so
far**, it returns GTO advice for the decision you actually face — at
every street (preflop through river) and every supported table size
(heads-up, 3/6/9-max). One endpoint does this: **`POST /advise`**.

### Module map

    poker_solver/          the engine — no FastAPI dependency, enforced by
                           tests/test_package_boundary.py
      game_tree.py         betting trees (GameConfig preflop, StreetConfig postflop)
      cfr.py               solve() = exact CFR+ (2 players), mccfr_solve() = sampled (N players)
      equity.py            preflop equity + MultiwayEquityCache
      board_equity.py      board-aware pairwise combo equity
      multiway_board_equity.py   board-aware N-way combo equity
      hand_eval.py         hand ranking (prime-product lookup table, M48)
      chance.py            chance nodes — build_chance_node (exact) / build_mccfr_chance_branch (sampled)
      solver.py            the public solve_* API + derive_ranges_from_path
      library.py           canonical spot library (canonicalize -> lookup -> solve on miss)
      canonicalize.py      suit-isomorphism canonicalization
      combos.py, cards.py, starting_hands.py, abstraction.py, strategy_format.py

    api/                   one-way layering, no cycles: config <- caches <- solving <- main
      config.py            every tunable constant, each with its measured justification
      caches.py            _SolveCache + one instance per endpoint (self-registering)
      solving.py           all _get_or_solve_* / _query_* / _advise* orchestration
      main.py              routes, validation, response shaping, app wiring
      schemas.py           Pydantic request/response models

    frontend/src/          React + TypeScript (Vite)
      components/          AdviseSolver is the front door; the rest are narrower demo tools

### Where to change what

| To change… | Go to |
|---|---|
| a cost cap or iteration budget | `api/config.py` (each constant carries its measurement) |
| how a request is answered | `api/solving.py` |
| a URL, status code, or response shape | `api/main.py` + `api/schemas.py` |
| solving itself | `poker_solver/` |

### Known constraints — read before "improving" these

- **6-max/9-max preflop budgets are 300 iterations, and raising them
  makes output WORSE, not better.** MCCFR diverges on this hand pool:
  AKs's UTG fold rate runs 15.6% (300) -> 48.7% (3k) -> 92.4% (30k).
  Cost is not the constraint. Pinned by
  `test_six_max_convergence_still_diverges_with_more_iterations`. The
  real fix is architectural (mask a hand's contribution in
  `_mccfr_recurse` rather than feed it a placeholder) — see M27, M63.
- **Multiway postflop answers an easier question than heads-up.** Its
  preflop leg solves over `DEMO_MULTIWAY_HANDS`' 8 classes, not 169.
  That is why multiway timings look faster; treat the advice as
  correspondingly thinner.
- **`trained` / `range_confidence` / `source` exist because output can
  look confident and be fabricated.** Don't strip them for tidiness.
- **The canonical-library path returns `trained: null`** — it persists
  only a flattened strategy dict. That is a real limitation, surfaced
  as an explicit null rather than hidden.
- **Five `*_from_path` routes are deprecated** (superseded by
  `/advise`), still functional. New callers should use `/advise`.

### Verification

    python -m pytest tests/ -v     # 750 backend tests
    cd frontend && npm test        # 145 frontend tests
    cd frontend && npm run lint && npx tsc --noEmit

### Further reading

- **`docs/milestones.md`** — the full milestone log (M8-present): what
  was built, every measured number, and the corrections later
  milestones made to earlier claims.
- **`docs/project-audit-2026-08-21.md`** — whole-project audit:
  redundancy findings, endpoint benchmarks, prioritized recommendations
  (all 7 resolved, M58-M65).
- **`docs/full-table-diagnostic-2026-08.md`** — the earlier engine
  diagnostic that drove M27-M34.

## v1 scope

- Engine: Python (NumPy for hot paths).
- Preflop only — RFI / 3-bet / 4-bet / jam ranges for a chosen effective stack
  depth. No board cards, no postflop streets (yet).
- Heads-up (2 players) only, not multiway.
- CFR+ solver over a 169-starting-hand-class abstraction (single bet size per
  street, 4-raise cap before forced jam-or-fold).
- Backend: FastAPI (`GET /solve/{stack_bb}`), on-demand solve with an
  in-process cache + startup pre-warm of common stack depths.
- Frontend: React + TypeScript (Vite) in `frontend/`. `npm run dev` for a
  hot-reloading dev server (proxies `/solve` to FastAPI on :8000);
  `npm run build` produces `frontend/dist/`, which `api/main.py` serves
  directly at `/` when present. See `frontend/README.md`.

Full postflop support is out of scope for v1 but the module boundaries (e.g.
an injected `payoff_fn` at terminal tree nodes) are meant to allow adding it
later without a rewrite.

## v2 progress

The per-milestone log that used to live here is now
**`docs/milestones.md`** (M65) — 58 entries, M8 to the present. The
narrative threads below explain the *shape* of the work; that file has
the detail, the measurements, and the corrections.

v2 grew the engine from heads-up preflop into a full-table, any-street
advisor. Two direction calls from its Phase C (postflop) design pass are
still load-bearing today and worth knowing before reading any of it:

- **Postflop works in concrete two-card combos, not the 169-class
  abstraction.** Blocker effects are a first-order postflop concern in a
  way they are not preflop.
- **Chance-node machinery was built one street at a time** — a flop-only
  tree with runouts averaged at the terminal came first, before
  multi-street chaining.

## v3 vision (future) — live-table advisor

Discussed with the user while scoping M16, recorded here rather than
left implicit: the longer-term goal beyond v2's demo/range-chart
tooling is a live-table advisor — a user mid-hand describes what
actually happened (any action sequence, any street, eventually
multiway) and gets advice grounded in a real solve for that exact
situation, not a curated demo range.

Two gaps identified when scoping this, pulling in different directions:
**flexible situation input** (`solve_flop*` only ever consumed curated
hardcoded ranges or, as of M15, one fixed preflop line — M16 is the
first general step past that, M23 the second, M24 the third, M25 the
fourth and last) and **real-time speed** (measured solve times, M12-M14,
run ~20s to several minutes even for small ranges). The user chose
flexible input first, since speed work is easier to scope once the
shape of query it needs to serve fast is known. Both gaps are now
closed end to end: the 4-phase real-time-speed roadmap (M17-M21), wired
into a live endpoint by M22, connected to a real, user-derived situation
by M23, exposed as a real live endpoint accepting an untrusted
action-path description by M24 (`POST /solve_flop_from_path` — a hit
costs ~0.15-0.2ms, a real derived-situation miss ~17-21s, capped per
M24's own Finding 1 from what would otherwise be hours), and finally
given a real interactive frontend by M25 (`POST /preflop_walk` plus a
rebuilt `ActionPathSolver.tsx`, replacing M24's own curated 3-preset
selector with a general step-by-step wizard over the exact same, fully
general backend M24 already shipped).

**M26 update — turn-level advice ships too, extending both threads
above one street further:** `POST /solve_turn_from_path` reaches a real
turn decision (not just a flop-level number improved by real turn
action baked in), and confirms the real-time-speed thread's own
`solve_flop_turn`/`solve_flop_to_river` (M12/M13) had already computed
real turn/river strategies all along — reading one out live cost
nothing new (~0.04ms, after a solve that was already being paid for).
A real, caught-before-shipping finding along the way: the derived-range
cap that works for the flop (`MAX_PATH_QUERY_CLASSES_PER_SIDE=6`) does
*not* carry over to the turn — `solve_flop_turn`'s steeper cost curve
turned the same cap into a 454s real request; a separately-measured,
smaller cap (`MAX_TURN_PATH_QUERY_CLASSES_PER_SIDE=2`) brought it back
to ~46s, in the same bracket `/solve_flop_to_river` was already
accepted in. What remains, deliberately: river-level advice one street
further (already de-risked cost-wise by this milestone's own
measurements — a two-hop river walk measured ~0.002ms), an interactive
flop-action wizard (this milestone's own flop-line input is a curated
preset dropdown, mirroring `ActionPathSolver.tsx`'s own M24-before-M25
history), and multiway postflop solving — the only thing across this
entire multi-milestone thread that has never been scoped at all.

**M29 update — the specific, common exception to that last line ships:**
true 3+-live-player postflop solving remains unscoped, but a real
multiway-*origin* hand that folds down to two live players now gets
real flop/turn advice through both live endpoints and both wizard
frontends — `poker_solver/game_tree.py`'s new `postflop_action_order`
(a real poker rule, not a heads-up-only guess) correctly maps whichever
two positions actually survive, at any origin table size, closing the
last of the "three duplicated position-unpack" sites the diagnostic's
§4 named. A related, previously-unknown gap surfaced and fixed along
the way: `derive_ranges_from_path`'s own reach-multiplication had no
confidence signal, and a real deep 6-max line was measured producing an
exactly-uniform, fabricated-looking derived range — `PathScenario`
gained its own `trained` field for this reason, mirroring M28's signal
one layer earlier in the pipeline (not yet threaded through to either
endpoint's own response — a named, deliberate gap, not a silent one).

**M46 update — river-level advice ships, closing this thread's last
open street — and corrects M26's own "already de-risked" claim.** M26
measured "a two-hop river walk measured ~0.002ms" and called river-
level advice already de-risked cost-wise on that basis. That number was
real but measured the wrong thing: reading a chance-branch lookup off
an *already-solved* `StrategyResult` is indeed nearly free — but the
`solve_flop_to_river` SOLVE itself, at a real derived (not the tiny
fixed 2-combo demo) range, is the actual cost, and M46 measured it
directly for the first time: 14-43s depending on combo-pool cap, far
from "de-risked." The corrected, now-actually-measured finding is
`RIVER_PATH_QUERY_MAX_COMBOS_PER_SIDE`'s own comment in M46's entry
above. What remains: an interactive flop-action wizard (still a curated
preset/fixed line, the same M24-before-M25-style gap this thread has
carried since M26), and multiway postflop solving beyond the turn
(M44's own turn-depth work is heads-up's only sibling so far; multiway
river-from-path is unscoped).

### The real-time-speed roadmap

Picked up after M16: real-time speed splits into genuinely different
levers, and the first one tried didn't pan out. A "batch board-equity
computation across matchups" attempt (chunking `build_board_equity_
table`'s per-pair `hand_eval.best_hand_rank_batch` calls into fewer,
larger ones — exactly what that module's own M10-era comment had
flagged as "the natural next optimization") was implemented and
measured before being trusted: at the same 23/~85-combo checkpoints
M10 used, it delivered only ~0-13% speedup, not the assumed win.
Profiling why: `best_hand_rank_batch`'s own vectorized computation
already accounted for ~81% of total time even in the *original*
per-pair implementation, and that cost scales with total data volume
(N² combo pairs × runout samples), not with how many separate Python
calls it's split across — so consolidating calls removed overhead that
was never the dominant cost. Discarded (never committed) once measured
— a real, cheap-to-discover dead end, not a hidden failure.

Given that ceiling, the deepest available path is a 4-phase program,
each phase depending on the one before it:

1. **Card abstraction (M17)** — bucket strategically-similar combos
   together, shrinking N directly. Attacks the O(N²)/O(N) cost at its
   root, the thing the failed batching attempt structurally couldn't do
   (it only ever reduced the constant factor around a fixed N).
2. **Canonicalization** — recognizing when two situations (board,
   action-history shape, stack depth) are strategically the same, so a
   library lookup can hit instead of every situation being unique.
3. **Offline precomputed spot library** — batch-solve a broad set of
   canonical situations ahead of time, no live time pressure, stored
   indexed by phase 2's canonicalization.
4. **Live query path** — a real situation (via M16's `derive_ranges_
   from_path` for the action history, hero's real hand, the actual
   board) gets canonicalized and looked up; a hit is instant, a miss
   falls back to an on-demand solve.

Card abstraction has to come first: precomputing exact-combo spots
doesn't achieve enough compactness to build a real library against.

**M18 update — phase 1's own live-solve speedup didn't materialize,
measured, not assumed:** wiring card abstraction into a real
`solve_flop`-shaped CFR solve (M18) found no meaningful speedup at 23-
or ~85-combo scale (0.95x-1.11x — break-even to slightly slower) and
measurably worse strategy accuracy than its own equity-level error
predicted. The reason traces cleanly to M17's own finding:
equity-table construction, not the CFR tensor step, dominates total
cost at these scales, and bucketing can only ever *add* a bucket-table
build on top of the full N×N equity table already required to derive
the bucketing signal — so a miss on the phase-3 library still costs
roughly what it costs today, not less. This changes point 4 above:
card abstraction doesn't make an on-demand miss cheaper by itself.
Phases 2-3 (canonicalization + an offline precomputed library) remain
the real lever for live speed, since they sidestep live equity-table
construction entirely on a hit — that's where M18's own finding says
the actual cost lives. Card abstraction may still matter for *offline*
library-building cost (batch-solving many canonical situations ahead
of time, where CFR iteration count/tensor size matters more relative
to a one-time equity build per situation) — not measured yet, a real
open question for whichever milestone scopes phase 3.

**M19 update — phase 2 (canonicalization) ships as a standalone
primitive, same pattern M17 set for phase 1:**
`poker_solver/canonicalize.py` provides exact, lossless suit-relabeling
canonicalization for boards and hole cards, plus bucketed stack-depth
rounding — the piece a future phase-3 library will actually index by.
Not wired into anything live yet. One real correctness finding along
the way: a naively-simple single-pass canonicalization algorithm was
measured against the true suit-isomorphism minimum before being
trusted, and found to under-collapse paired-rank boards (1,911 distinct
flop forms instead of the true 1,755) — fixed by searching the full
24-permutation suit-automorphism group instead, which also turned out
simpler than the naive design it replaced. Confirmed by exhaustive
enumeration: 22,100 flops collapse to 1,755 canonical forms, 270,725
turns collapse to 16,432, and 2,598,960 rivers collapse to 134,459 —
real numbers a future phase-3 library-sizing decision can use directly.

**M20 update — phase 3 (an offline precomputed spot library) ships as a
standalone primitive with its key contract proven, not just assumed:**
`poker_solver/library.py` batch-solves real boards, dedupes by M19's
canonical (board, bucketed-stack) key, and stores each distinct
canonical solve. The real risk this phase could have gotten wrong
silently — whether a canonical hit actually serves *any* isomorphic
real board, or only the literal board a solve happened to run against —
was resolved by constraining `build_library` to class-frequency ranges
only (never raw asymmetric combo dicts, which don't have the suit-
blindness property the whole scheme depends on) and confirmed end to
end: a library built by solving one real board is queried with a
*different*, merely suit-isomorphic real board never solved directly,
and the returned strategy matches a fresh direct solve exactly. Phase 4
(a live query path with canonicalize-then-lookup-then-fallback-to-
on-demand-solve, plus API/frontend wiring) is the roadmap's final,
still-unscoped phase — now unblocked, since phases 2 and 3's contracts
are both proven, not just built.

**Correction, from M21:** that exact-match claim above held for the
specific board pair M20's own test used, but only because that pair's
second board was, unnoticed, literally the first board's own canonical
form (an identity suit-map). Tested against a genuinely different real
board instead, the match is not bit-exact — flop-level equity is Monte
Carlo sampled, and the deck's suit-dependent iteration order means the
same seed draws different specific runouts for two differently-suited
isomorphic boards. The actual crux property (a hit correctly *serves*
any isomorphic board without re-solving) still holds; see M21's own
entry above for the precise, corrected statement and the fix applied
to the test that first surfaced this.

**M21 update — Phase 4 (a live query path) ships, closing the
roadmap's engine-level work:** `poker_solver/library.py`'s `query_
strategy` completes the loop this roadmap set out to build in M17:
canonicalize a real query, look it up, return instantly on a hit, fall
back to an on-demand solve on a miss (via `build_library`'s own logic,
not duplicated), and cache the result in place so the next hit on that
canonical spot — or any real board merely isomorphic to it — really is
instant. All four phases are now done: card abstraction (M17) was
tried and found not to be the lever (M18: equity-table construction,
not the CFR tensor step, dominates cost, so shrinking hand count
doesn't shrink the real bottleneck); canonicalization (M19) and an
offline precomputed library (M20) sidestep that bottleneck entirely on
a hit instead of trying to speed it up; this phase wires hit/miss into
one live entry point and measures the real payoff: a hit costs
**~0.15ms**, a miss costs **~0.95s** (in the same ballpark as M20's own
~0.92s/board figure, since a miss *is* a one-board `build_library`
call), a **~6,313x** ratio — the concrete, measured answer to the
question this roadmap exists to answer, not an assumed one.

What's deliberately still not done, now that the roadmap's own
engine-level work is complete: no `api/main.py`/frontend wiring (a live
endpoint calling `query_strategy` against a real, persistent, shared
library, including a concurrent-miss serialization decision this
milestone didn't need to make), and no connection to M16's `derive_
ranges_from_path` (translating a real, user-described action history
into `query_strategy`'s `hero_classes`/`villain_classes` inputs — a
mostly direct fit, since `derive_ranges_from_path` already returns
`StartingHand`-keyed ranges for a preflop `StrategyResult`, but with
one real wrinkle worth naming precisely: `PathScenario.stacks` is a
per-position dict, not the single `effective_stack_bb` float `query_
strategy`/`solve_flop` expect, so an arbitrary path needs an explicit
"both live positions' remaining stacks are equal here" check before
that hookup is safe). Both are natural next milestones — the same
two-engine-primitives-then-one-wiring-milestone pattern M12/M13-
before-M14 already established.

**M22 update — the first of those two follow-ons ships:** `GET /solve_
flop_cached` calls `query_strategy` live, against a fixed demo range
(not yet a real user-described one — that's M23's job). Measured
through the real endpoint: a hit costs **~0.20ms**, a miss costs
**~1.55s**, a **~7,763x** ratio. The connection to `derive_ranges_from_
path` remains open.

**M23 update — the second follow-on ships too, closing both:**
`poker_solver/library.py`'s `query_strategy_from_path` bridges a real
preflop `StrategyResult` + a real walked `action_path` into `query_
strategy`, completing the loop M21's own write-up predicted. The
"stacks equal" check M21 anticipated turned out to be the wrong check
— the correct one is `isinstance(path_scenario.node, TerminalNode)`,
proved sufficient (not just necessary) from `game_tree.py`'s
no-side-pots construction; see M23's own Phase C entry for the full
argument. Still not done: a live endpoint accepting a real, untrusted
action-path description (deliberately deferred, same reasoning as
every other bridge milestone in this roadmap) and multiway postflop
solving (out of scope for this entire roadmap, not just this
milestone).

**M24 update — the live endpoint ships too, closing this roadmap's
product-surface work:** `POST /solve_flop_from_path` finally exposes
the full canonicalize -> library -> path-derived-range chain to a real,
untrusted client — the thing M21 first named as remaining, that every
milestone since (M22, M23) closed one piece of. Getting there safely
required two real findings, not just wiring the pieces together: an
uncapped derived range would have cost hours per request (Finding 1,
fixed with a request-time top-K cap, engine layer untouched), and
sharing one canonical-key library across different real situations
would have silently corrupted answers (Finding 2, fixed with a
partitioned per-`(action_path, stack_bb, iterations)` library). Real
measured numbers: a capped miss costs ~17-21s, a hit ~0.1-0.7ms.
Multiway postflop solving remains the only thing this whole roadmap +
its flexible-input companion thread never scoped — explicitly future
work, not this project's v2.

## Engine is standalone

`poker_solver/` has zero dependency on the API or any web framework — it's a
plain library usable on its own (`import poker_solver; poker_solver.solve_preflop(...)`).
This is enforced, not just true by convention: `tests/test_package_boundary.py`
scans every file under `poker_solver/` and fails the build if it ever imports
`fastapi`, `starlette`, `uvicorn`, or `api`. `api/` depends on `poker_solver`,
never the reverse.

Dependencies are split accordingly:
- `requirements.txt` — the engine only (`numpy`). `pip install -r requirements.txt`
  is enough to use `poker_solver` standalone.
- `requirements-api.txt` — adds the FastAPI backend (`-r requirements.txt` + `fastapi` + `uvicorn`).
- `requirements-dev.txt` — everything needed to run the full test suite (`-r requirements-api.txt` + `pytest` + `httpx2`).

## Workflow rules

- **Always work on a branch.** Never commit directly to `main`. Create a
  feature branch for every change, however small, and only merge into `main`
  when the user explicitly says to merge.
- **Tests are mandatory.** Every function gets a test.
  - Python: follow the `tests/` + pytest convention (one test module per
    source module, e.g. `poker_solver/foo.py` -> `tests/test_foo.py`).
  - Frontend: Vitest + React Testing Library, colocated as `*.test.ts(x)`
    next to the file it tests (e.g. `frontend/src/hands.ts` ->
    `frontend/src/hands.test.ts`).
- **Re-run the full suite after every change** — `python -m pytest tests/ -v`
  and, for anything under `frontend/`, `npm test` there too — before
  considering any change done, not just the tests for the file just touched.
- **Record each milestone in `docs/milestones.md`**, not in this file —
  CLAUDE.md is loaded into context every session and holds current
  state; the log is history, consulted by search. Keep entries in the
  established voice: what shipped, the real measured numbers, findings
  and corrections, and what was deliberately deferred. If a milestone
  changes current state (a new constraint, a moved module, a new
  entry point), update the Current state section here too.
- Ship one coherent improvement per PR (matches how this project started:
  scaffold -> missing-test PR -> merge).
