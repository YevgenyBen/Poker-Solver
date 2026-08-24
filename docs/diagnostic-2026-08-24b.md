# Second maximal diagnostic — 2026-08-24 (M125)

Run immediately after M124, deliberately targeting ground M124 did *not*
cover, plus re-verifying what M124 and M119 changed.

M124 probed static structure, constraints, latency, live play, coverage.
This one probes **cross-endpoint agreement after F30, persistence,
concurrency, memory, stack extremes, the dev proxy, response-schema
completeness, and the running frontend in a real browser** — the last of
which no diagnostic in this project has ever done.

**Headline: correctness is solid everywhere it was checked. All three
findings are about what the product TELLS a user, not what it
computes.** That is the third consecutive diagnostic where the defect
lived in user-facing text rather than in the engine.

---

## 1. Correctness — nothing found

### Cross-endpoint agreement survived F30

M119 changed range construction for **every postflop solve in the
project**. M107 (audit round 7) had verified `/advise` agrees with the
deprecated `*_from_path` endpoints, but that verification predates the
change, so it was re-run rather than assumed:

| comparison | hands/combos | max delta |
|---|---|---|
| preflop `/advise` vs `GET /solve` | 169 | **0.0** |
| flop `/advise` vs `/solve_flop_from_path` | 127 | **0.0** |
| turn `/advise` vs `/solve_turn_from_path` | 57 | **0.0** |

Exact agreement at every street, same position and same pot on both
sides. F30 moved both paths identically, which is what a change in a
shared engine layer should do.

### Library persistence round-trips exactly

`build_library` → `save_library` → `load_library` → `lookup_strategy`:
entry count preserved, 15 combos, **max delta 0.0**. Nothing in the JSON
round trip loses or distorts a stored solve.

### Stack extremes behave

1bb through 10,000bb through `/advise`:

| stack | result |
|---|---|
| 1bb | 200 — only `fold`/`call_or_check` offered, correct: the BB is all-in from posting (M117's boundary) |
| 2bb | 200 — `all_in:2.00` appears, no sized raise |
| 5bb | 200 — `raise:2.50` and `all_in:5.00` |
| 500 / 1,000 / 10,000bb | 200 — sizes scale correctly |

Every row a valid probability distribution (**worst sum error 0.0** at
every depth), and **no advice named an unaffordable bet at any depth**.

### The frontend actually works

Driven in a real browser against real servers — never done before in
this project's diagnostics. The Advisor answered a heads-up preflop
request in 3.51s and a 6-max one in 51.3s, rendered hero's hand
(`AsKs — call_or_check 22%, raise:2.50 75%, all_in:100.00 3%`) and the
full 169-class grid. **Zero console errors.** Every network request
succeeded.

**M123's corrected caveat reaches a real screen verbatim**, including
the replacement wording: "at 6-max the fold frequency is flat across
UTG, MP, CO and BTN, where real GTO play widens from roughly 15% of
hands under the gun to roughly 45% on the button." The withdrawn
"button tighter than UTG" claim is gone from the product, not just from
the config file.

---

## 2. Findings — all three are about what the user is told

### E2 — the 9-max range chart is served with no confidence signal *(high)*

`solver_confidence` and `sizing_confidence` exist on exactly **one of
eleven** response models:

| model | honesty fields |
|---|---|
| `AdviseResponse` | all five |
| `SolveResponse`, `FlopSolveResponse`, `TurnPathQueryResponse`, `RiverPathQueryResponse`, `FlopMultiwayPathQueryResponse`, `TurnMultiwayPathQueryResponse` | `trained` only |
| `EquityResponse`, `FlopQueryResponse`, `FlopPathQueryResponse`, `PreflopWalkResponse` | none |

`SolveResponse` is what `GET /solve/{stack_bb}?players=9` returns — the
**9-max preflop range chart**. CLAUDE.md is unambiguous about it:

> 9-max preflop output is NOT reliable (M68, measured). T7s folds only
> 12.5% under the gun at 9 handed, where it should be near 100% […]
> **Don't present 9-max advice as authoritative.**

M76 added `solver_confidence: "low"` for exactly this, and it is
attached to `/advise` alone. A caller of `/solve?players=9` — which is
what the frontend's own Preflop Ranges tab uses — receives a complete,
confident-looking 169-class chart of an under-trained solve with nothing
in the payload saying so.

### E3 — the frontend's multiway caveat is factually wrong *(high)*

`PreflopRangesPage.tsx` tells users the multiway chart is:

> a small curated hand subset (MCCFR), not the full 169-hand exact solve

`MULTIWAY_PREFLOP_HANDS` contains **169 hands**. M67 replaced the old
8-class curated pool with the complete one; this sentence describes the
world before that.

Being out of date is the smaller half. The larger half is that it points
at the **wrong cause**, in the reassuring direction: a reader concludes
the method is sound and only the sample is small. The truth is inverted
— the pool is complete, and at 9-max the *solve does not converge*. It
also lumps 3-, 6- and 9-max together as "demo" where CLAUDE.md separates
them sharply: 3-max and 6-max are "in much better shape", 9-max is the
one that must not be presented as authoritative.

### E1 — the dev proxy invariant is unenforced *(medium)*

`vite.config.ts` proxies API calls per prefix. All 16 routes are covered
**today** — checked mechanically, zero gaps.

But the config's own comment records this breaking **three times**
(M10's `/equity`, M25's `/preflop_walk`, M56's `/advise`), and says why
nothing catches it:

> Caught by live browser verification (a real 404), NOT by the unit
> tests, which stub fetch and so can never see a proxy gap.

A route added tomorrow whose name does not start with an existing prefix
falls through to the SPA's `index.html` and 404s in dev, and only a human
clicking around notices. Same shape as M124's D3: an invariant that is
currently true, has failed before, and nothing asserts.

---

## The pattern worth naming

M123 found user-facing text repeating a withdrawn measurement. M124's
D2 found the pre-warm failing invisibly. This diagnostic's three
findings are *all* about the gap between what the project knows and what
it tells anyone.

Every internal record is correct. CLAUDE.md knows 9-max is unreliable;
`api/config.py` knows it; `LOW_CONFIDENCE_TABLE_SIZES` names it. The
knowledge simply stops at `/advise` and at one stale sentence in a React
component. **Internal accuracy does not propagate outward on its own,
and this project's own history now shows it three times running.**

---

# Acted on — all three, in priority order (M125)

## E2 — the range-chart endpoint carries the caveats now *(fixed)*

`SolveResponse` gained `solver_confidence` / `sizing_confidence` and
their reasons, from the same constants `/advise` uses. Verified live:

| players | solver_confidence | sizing_confidence |
|---|---|---|
| 2 | high | high |
| 3 | high | **low** |
| 9 | **low** + reason | **low** + reason |

Two tests: the same parametrized table `/advise`'s own guard uses, plus
one asserting the two endpoints **agree** on every table size, so they
cannot drift apart on the same question from the same cached solve.

## E3 — the frontend says what is true *(fixed)*

The subtitle now reads "all 169 hand classes, sampled (MCCFR)" for
multiway and "all 169 hand classes, exact (CFR+)" heads-up, and the
reliability claims come from the API rather than from hardcoded prose —
so the page cannot go stale relative to the measurement again. The
low-confidence caveats render on this tab for the first time.

**A second stale claim surfaced while fixing it.** `AdviseSolver.tsx`'s
fallback text still read "The fold-vs-play call is sound" — the exact
claim M111 withdrew and M123 corrected in `api/config.py`. It had
survived two milestones in the copy a user reads. Corrected.

Two frontend tests: one that the API's caveats reach the screen, one
that the page no longer claims a curated subset.

## E1 — the dev proxy invariant is enforced *(fixed)*

A test that reads both the FastAPI route table and `vite.config.ts` and
fails on any route no proxy prefix covers. It lives on the Python side
because that is the only place both are visible — the frontend suite
stubs `fetch` and structurally cannot see a proxy gap, which is exactly
why three previous occurrences reached a browser before anyone noticed.

Mutation-checked: adding a route named `/ranges_export` fails the test
with the route named and the fix stated.

---

## Suites

Backend **948 passed** (up from 942). Frontend **156 passed** (up
from 154).
