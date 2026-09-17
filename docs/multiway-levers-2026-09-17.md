# M264 — which lever moves multiway advice toward Pluribus?

M262 found multiway postflop advice **worse than a card-blind prior**
against 579 Pluribus decisions: −0.184 at −9.70σ. The cause is betting
frequency. Checked to, Pluribus bets 19% and we bet 59%.

Every multiway configuration change before this was refused for want of
a reference (F46/M163/M245). M262 provides one. This study tests the
standing candidates, one at a time, on the same 579 decisions.

## Arms

Each arm runs in its own process.

| arm | change | hypothesis |
|---|---|---|
| baseline | none (re-run for comparable timing) | — |
| **iters4** | 4× the multiway postflop iteration budget (flop, turn, river) | rows sit near the starting mass, which is 80% aggressive at an unfacing node with five actions |
| **cap26** | range cap 8 → 26 classes per position | the modelled ranges are too thin (M245 measured width as inert against *seed noise*, never against an outside reference) |
| **ensemble4** | average 4 independent solves (M169) | sampling noise drives the over-betting |
| **one_size** | one bet size (0.75×) instead of three — **diagnostic only** | action-count dilution: more sized actions means more starting mass on betting |

## Reading rule — fixed before running

**The score** is the M262 one: our probability on Pluribus's action,
compared with the baseline arm and paired per decision. It is reported
beside the arm's aggression when checked to, against Pluribus's 19%.

**An arm is ADOPTED only if all of these hold:**

1. **Improvement:** it beats the baseline on paired p at **2σ**.
2. **Direction:** its checked-to aggression moves **toward** 19%.
3. **Latency:** its multiway postflop p90 stays under **5 s** at this
   machine's state.

**The `one_size` arm is never adopted as a menu,** whatever it shows. It
removes sizing advice players rely on (M209–M220). Its job is to say
whether dilution is the mechanism. If it improves at 2σ, the fix is a
*prior* that is not action-count weighted, and that is a separate change
with its own study.

**If no arm clears the bar,** the disclosure stays as it is, and the
candidates are recorded as measured against an outside reference.

## Result

All five arms replayed all 579 decisions, with zero skips.

| arm | p | vs baseline | vs card-blind prior | bets when checked to | raises / folds facing | p50 | p90 | max |
|---|---|---|---|---|---|---|---|---|
| baseline | 0.451 | — | −0.180 (−9.39σ) | 0.589 | 0.385 / 0.407 | 0.98 s | 1.40 s | 2.92 s |
| **iters4** | **0.558** | **+0.107 (7.86σ)** | −0.073 (−3.86σ) | **0.470** | 0.326 / 0.452 | 2.87 s | **3.96 s** | 7.41 s |
| cap26 | 0.458 | +0.007 (0.54σ) | −0.173 | 0.609 | 0.376 / 0.393 | 3.28 s | 4.91 s | 12.23 s |
| ensemble4 | 0.453 | +0.002 (0.23σ) | −0.178 | 0.587 | 0.401 / 0.381 | 3.78 s | 5.21 s | 9.50 s |
| one_size (diagnostic) | 0.620 | +0.169 (10.14σ) | **−0.011 (−0.63σ)** | 0.351 | 0.306 / 0.494 | 0.89 s | 1.29 s | 2.67 s |

Pluribus, for reference: bets **0.194** when checked to; facing a bet it
raises 0.111 and folds 0.593.

### Reading, by the rule

- **`iters4` is ADOPTED.** It is better at 7.86σ, it moves aggression
  toward the reference (0.589 → 0.470), and p90 is 3.96 s, under 5 s.
  - **Still below the prior:** −0.073 at −3.86σ, so the multiway
    disclosure stays and its figures are re-measured on this arm.
  - **Cost:** 22 of 579 decisions exceed 5 s (max 7.4 s). The rule was
    set on p90, and the tail cost is recorded rather than hidden.
- **`cap26` and `ensemble4` do nothing,** and both cost latency.
  - This agrees with M245 (width inert) and M169 (ensembles narrow the
    middle and leave the answer).
  - It is now measured against an outside reference rather than against
    seed noise.
- **`one_size` names the mechanism.**
  - With one sized bet instead of three, multiway advice reaches the
    card-blind prior (−0.011, not separable), and checked-to aggression
    falls to 0.351.
  - **Why:** regret matching starts every row uniform over its actions.
    With check, three bet sizes and all-in, **80% of the starting mass is
    aggressive**, and an under-converged row keeps much of it.
  - **Supporting evidence:** in the baseline, rows whose top action is
    under 0.4 bet 0.76 of the time, against 0.41 for decisive rows.
  - **The fix is not a smaller menu,** which M209–M220 showed players
    need. It is a starting strategy that does not weight betting by how
    many bet sizes exist. That is a change to the solver and needs its
    own study (recommendation A4b).

### What changed in the product

- **Multiway postflop budgets go ×4:**
  - flop 1000 → 4000;
  - standalone turn 1000 → 4000;
  - standalone river 50 → 200 (it had been running at the chained
    solver's default).
- **Chained caps are unchanged.** The deprecated chained endpoints keep
  their own ceilings, because an iteration there costs a whole flop leg.
- **`MULTIWAY_BET_NOTE` quotes the re-measured figures:**
  - checked to: 47% against 19%;
  - weaker half of hands: 35% against 14%;
  - facing a bet where the gate fires: the reference raised 26%, called
    49% and folded 26%.
- **Not re-measured here:** the reproducibility figures (M245/M254) were
  taken at the old budget. More iterations should make multiway answers
  *more* stable, so those figures now likely overstate the instability.
  That direction is safe for a warning, but it is stale, and it is queued.
