# Real online hands through `/advise` (A5, M266)

**Question.** Can the product answer the spots real players actually
meet? Every earlier benchmark generated its own spots, and M262 showed
that replaying real hands finds what generated spots miss.

**Method.**

- **Sample:** 1,200 decisions, one per hand. The hands are HandHQ 2009
  online hands from the local store (`bench.hand_db`).
- **Filter:** clean hands with 2, 3, 6 or 9 players and an effective
  stack of 2–200 bb.
- **What each decision keeps from the real hand:**
  - the real stack;
  - the real line;
  - real bet sizes, mapped to the nearest size the response offers.
- **Cards:** hole cards are hidden in these logs, so each player gets a
  random legal hand (seed 5).
- **9-max:** held to the 100 bb bucket. The harness runs no background
  warmer, and an unwarmed 9-max depth costs about 525 s.
- **Checks:** every 200 goes through `bench.advise_checks.response_defects`.
- **Where it lives now:** the harness is `bench/real_replay.py`.

## Result

| outcome | decisions |
|---|---|
| answered | **1,199** |
| refused | 0 |
| could not be expressed | 1 |
| **response defects** | **0** |

By table size, the answered decisions were:

| players | preflop | flop | turn | river |
|---|---|---|---|---|
| 2 | 388 | 80 | 38 | 30 |
| 3 | 89 | 15 | 5 | 3 |
| 6 | 400 | 85 | 33 | 15 (+1 not expressible) |
| 9 | 14 | 3 | 1 | 0 |

Other figures:

- **Confidence:** "low" on 5.4% of answers.
- **Multiway share:** 12.3% of postflop answers had 3 or more players
  still in.

### The one line it could not follow was a product defect (fixed)

The hand was a six-max 5-bet pot:

- the line was open, 3-bet, 4-bet, 5-bet to 30 bb, then a call;
- the preflop model allows four raises and forces the fourth all in.

So in our model the pot is all in before the flop.

- **What the API did:** asked about the turn, it replied 422 **"stack_bb
  must be positive"** to a request whose `stack_bb` was 100.
- **Heads-up too:** the same message came back for any board after a
  heads-up all-in call preflop.
- **The fix:** `_validate_preflop_path_shape` now refuses such a line by
  name before any solve. The message says every remaining player is all
  in and that the preflop model makes its fourth raise all in.
- **Measured:** 0.08 s at a cold 73 bb six-max depth.
- **Tests:** heads-up flop and turn, and six-max, in both directions.

### Latency, and a regression M264 shipped

| | n | median | p90 | over 5 s | over 30 s |
|---|---|---|---|---|---|
| all answers | 1,199 | 0.01 s | 4.73 s | 95 | 12 |
| first request at an unwarmed multiway depth | 61 | 6.91 s | — | 38 | 12 |
| everything else | 1,138 | 0.00 s | 3.97 s | 57 | 0 |

**Cold depths.** All 12 answers over 30 s are the first request at a
six-max depth that had not been warmed yet.

- **Why the harness saw them:** it runs no lifespan, so A3's background
  warmer never ran.
- **In production:** the warmer covers these depths after startup.
- **Still true:** a player who arrives before the warmer finishes still
  waits.

**Multiway postflop slows with every extra live player:**

| street | live | n | median | max |
|---|---|---|---|---|
| flop | 2 | 154 | 3.97 s | (cold depths) |
| flop | 3 | 21 | 7.01 s | 10.18 s |
| flop | **4** | 7 | **13.54 s** | 18.35 s |
| flop | **5** | 1 | **14.37 s** | — |
| turn | 3 | 7 | 6.42 s | 8.32 s |
| turn | 4 | 1 | 10.63 s | — |

**This is M264's 4× budget.** M264 quoted one p90 over all its
multiway decisions (3.96 s). Those decisions were 93% three-live, so the
p90 hid the wider pots.

Split by live count, on the same Pluribus replay:

| live | x4 vs old budget, paired | flop median, old → x4 |
|---|---|---|
| 3 (n = 541) | **+0.110, 7.88σ** | 1.12 → 3.63 s |
| 4 (n = 38) | +0.056, **1.01σ** | **2.10 → 7.04 s** |

- **The trade at 4+ live:** 3.4× the latency for a gain that cannot be
  told apart from zero. Real stacks roughly double that latency again.
- **The change:** a pot with **4 or more live players** now keeps the
  pre-M264 budget on the flop and turn
  (`MULTIWAY_WIDE_POT_ITERATIONS`, 1,000). The river is unchanged at
  0.7 s.
- **Scope of the evidence:** the 4-live accuracy question is
  **underpowered, not answered**. This reverts a cost nobody measured a
  benefit for; it does not show that the benefit is absent.

### What real players are told

These are the advisory notes on the 891 answered postflop decisions:

| note | fires on |
|---|---|
| standing-aggression-caveat | 308 |
| flop-measured | 183 |
| street-isolation | 163 |
| drawy-board | 111 |
| facing-a-bet-cost | 96 |
| street-unmeasured | 77 |
| turn-independent | 62 |
| river-measured | 48 |
| costly-band | 29 |
| multiway-bet | 16 |
| bet-sizing-coverage | 6 |
| river-under-fold | 5 |

`turn-shove` did not fire once in this sample.

### Coverage: the larger problem (A9)

Only 2/3/6/9-player tables are supported, so **43.6% of real hands
cannot be asked about at all**. Five-handed hands alone are 19%. With
stacks over 200 bb excluded too, only **46%** of real hands can be
asked about. This replay measured the other 54%'s absence, not their
advice.

## Rules

- **Split a latency figure by live count.** A pooled p90 over a
  population that is 93% three-handed says nothing about four-handed
  pots.
- **Replay real hands after any budget change.** Real stacks cost about
  twice what the 100 bb Pluribus stack does.
