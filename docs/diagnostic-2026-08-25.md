# Deep maximal diagnostic — 2026-08-25 (M128)

M124 and M125 measured structure, constraints, endpoints and the browser
and found real defects. M127 then found a **4.9 GB cache** by *playing*
120 hands — something neither had done.

That is where this diagnostic starts. The gaps were never in what the
project checked; they were in **the shape the check was made in**. So
this one asks, per subsystem: what does the existing guarantee literally
promise, and what does a real caller actually need?

---

## Six checks. No new defects.

| check | result |
|---|---|
| evicted entries are genuinely freed | **yes** — weak references die on eviction, so the ceiling is real, not cosmetic |
| per-key locks are pruned with their entries | **yes** — 0 locks after 200 distinct keys at a ceiling of 4 |
| repeated **cold** solves agree | delta exactly **0.0** across three cache-cleared solves |
| board card ordering is irrelevant | delta exactly **0.0** across three orderings of one board |
| cache footprint after M127's fix | nothing over budget; **530 MB** combined ceiling |
| hero's hand does not corrupt the villain pool | correct — see below |

The first two matter specifically because of M127. A byte ceiling is
worthless if evicting does not actually release the memory, or if a
smaller structure leaks alongside the capped one. Both hold.

## Two near-misses, caught before they were reported

**"Pool size varies with hero's hand"** — 131 / 136 / 133 combos for
`AhKh` / `7s2c` / `QdQs`. That is correct: hero's own combo is
force-included (M76), and different hero cards block different villain
combos, so the legal pool legitimately differs.

**"Returned combos share hero's cards"** — 18 of 131 for `AhKh`. Also
correct, and the check was reading the field wrong. `strategy` is the
**acting seat's own range**; hero's combo is a member of it
(`hero_own_combo_present` is true for every hand tested). Its other
members are *alternative holdings for that same seat*, not hands held
simultaneously.

Both would have been published as defects on a less careful reading.
This is the eighth and ninth time in this audit that a check needed
checking before its result meant anything.

---

## The finding this diagnostic exists for, and a correction to M127

M127 measured a flopped set advised to raise **0.25%** at the shipped
ten-class cap and **40.2%** at 26, and concluded the narrow cap
*systematically* biases strong hands toward slow-playing.

**That conclusion was wrong**, and it was wrong in a way this project has
been bitten by before: it was read off **two points**. Sweeping nine caps
on the same spot:

| hero | 10 | 12 | 14 | 16 | 18 | 20 | 22 | 24 | 26 |
|---|---|---|---|---|---|---|---|---|---|
| `9s9d` set | .003 | .004 | .019 | .025 | **.771** | .071 | .122 | .393 | .402 |
| `QdQh` overpair | .007 | .006 | .006 | .031 | **.533** | .018 | .023 | .023 | .032 |

Both spike at eighteen classes and collapse again. Neither is monotone —
the overpair reverses direction **five times**. The real finding is worse
than a bias:

> **Postflop value-hand advice is unstable with respect to
> `MAX_PATH_QUERY_CLASSES_PER_SIDE` — a pure cost control with no
> strategic meaning, which the user never sees.**

**Not noise.** Solving twice at the same cap gives a delta of exactly
`0.0`; this solver is deterministic here. Each figure is exact for its
cap. The instability is real sensitivity, not sampling variance.

**Widening is not an escape.** Cost climbs steeply — one flop decision
goes from 10.8s at cap 10 to **52.1s at cap 26**, 4.8x — so no setting is
both affordable and stable.

### Same shape as M110 -> M111

M110 read a 1.7pp gap as "the button opens tighter than under the gun";
M111 measured the seed variance at 2.8pp and withdrew it. M127 read two
cap values as a systematic direction; nine values show chaos. Both times
the error was **treating a two-point difference as a trend**, and both
times the correction came from sampling the axis properly.

### What follows from it

Not "raise the cap" — that moves the answer unpredictably. The honest
response is the one this project already applies to preflop sizing
(M98): **tell the user.** The aggression axis postflop is not currently
trustworthy for value hands, and nothing in the response says so.
