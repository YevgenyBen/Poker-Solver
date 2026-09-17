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

*(Results follow.)*
