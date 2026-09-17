# M262 — multiway advice checked against Pluribus

**Multiway has never had an outside reference** (F46/M163/M245). No
converged multiway solve exists to compare against, which is why every
multiway figure in this project is either internal or a disclosure.

Pluribus is the 6-max agent that beat professionals. Its 10,000 published
hands carry every hole card and every action, and they are now in
`bench.hand_db` (M261). That gives us thousands of real decisions where a
strong player **acted**.

## What one hand can and cannot tell us

A recorded action is **one draw** from Pluribus's strategy, not the
strategy itself. So no single row is right or wrong. The measurement is
statistical: **how much probability does our advice put on what
Pluribus did**, over many decisions, compared with baselines that use
less information.

### The action scale

Actions are compared by kind:

- **fold**;
- **passive:** check or call;
- **aggressive:** bet, raise or all-in.

Size is compared separately and only descriptively, because Pluribus's
sizes and our menu differ.

### Scores, per decision

- **p** — our probability on Pluribus's kind;
- **log loss** — −ln(max(p, 0.001));
- **top-1** — whether our most likely kind is Pluribus's kind.

### Baselines, per cell

A cell is (street, live players 2 / 3+, facing a bet or not).

- **card-blind prior:** Pluribus's own frequency of each kind in that
  cell, **leave-one-out**. It knows the spot and nothing about the cards.
  **Our advice must beat this to show it reads the cards.**
- **uniform:** equal weight on every kind legal at the node.

## Cells

- **Multiway postflop:** every Pluribus decision with 3+ players live —
  about 580.
- **Preflop at 6-max:** a random sample, split by live count, 3+ and 2
  (M251's two-live cell).
- **Heads-up postflop, the CONTROL:** a random sample. This is the cell
  whose advice was validated against an independent solver (M221–M260),
  so it shows what "good" looks like on this scale.

## Replaying a hand through `/advise`

- **Preflop:** mapped by kind. `raise` means our next sized raise, and an
  all-in maps to `all_in`. Our menu (2.5 bb open, 3×, 2.2×, then all-in)
  differs from Pluribus's, so **our pot can differ from the real one**,
  and each row records both.
- **Postflop:** walked one action at a time. At each node our
  `modelled_bet_sizes` are read (asking as the player who actually
  acted, with their real cards). Pluribus's size maps to the nearest of
  ours, as a pot fraction for a bet or a multiple of the bet faced for a
  raise. The worst mapping ratio on the path is recorded.
- **Unrepresentable lines:** a line our tree cannot represent (a 422) is
  counted and skipped, never guessed.

## Reading rule — fixed before running

Each result is the **paired** difference between our score and the prior
over the same decisions: mean p and log loss, with sem.

| cell | our p minus prior p | reading |
|---|---|---|
| heads-up control | expected **> 0 at 2σ** | if not, the metric cannot see card-reading and nothing below is read |
| multiway postflop | > 0 at 2σ | our multiway advice carries card information a spot-only prior lacks, and it is quantified |
| multiway postflop | not separable | **no better than a card-blind tendency, from outside.** The disclosure should say so |
| multiway postflop | < 0 at 2σ | **worse than a card-blind tendency.** The disclosure must say so |
| preflop, 3+ live / 2 live | same three readings | same three readings |

**Secondary, M254's gate.** Within multiway postflop, rows the shipped
gate calls *stable* (top action ≥ `MULTIWAY_STABLE_MAX_TOP_ACTION`, and
not the river) should agree with Pluribus more than *split* rows do.

- A 2σ difference validates the gate from outside.
- No difference is recorded, and the gate is not changed on this
  evidence alone.

**What this cannot establish.**

- **That Pluribus is right.** It was trained for 6-max at 100 bb with its
  own sizes, and it is a strong agent, not an equilibrium.
- **A price in bb.** One sampled action has no EV.
- **Anything about other stacks or table sizes.**

## Result

**2,279 Pluribus decisions replayed; 2,278 represented.** The one miss
is a river raising war our tree can't express.

- **Mapping:** sizes mapped at a median worst ratio of **1.00** (p90
  1.35–1.50).
- **Pot drift:** our pot ran a median **1.10–1.15×** the real one,
  because our 2.5 bb open is larger than Pluribus's usual open.
- **Robustness:** restricting to rows mapped within 1.5× changes no
  conclusion.

### The pre-registered reading

Scores are our mean probability on Pluribus's action, beside the
card-blind prior. Differences are paired.

| cell | n | ours | prior | uniform | ours − prior | reading |
|---|---|---|---|---|---|---|
| **heads-up postflop (control)** | 699 | 0.568 | 0.511 | 0.457 | **+0.057, 3.58σ** | the metric sees card-reading: **proceed** |
| **multiway postflop** | 579 | **0.446** | **0.631** | 0.461 | **−0.184, −9.70σ** | **worse than a card-blind tendency** |
| multiway, flop | 350 | 0.433 | 0.643 | — | −0.210, −8.84σ | |
| multiway, turn | 159 | 0.492 | 0.605 | — | −0.112, −3.05σ | |
| multiway, river | 70 | 0.410 | 0.629 | — | −0.219, −3.76σ | |
| multiway, checked to / first in | 444 | 0.444 | 0.686 | — | **−0.242, −11.02σ** | |
| multiway, facing a bet | 135 | 0.453 | 0.449 | — | +0.004, 0.13σ | no better than the prior |
| **preflop, 3+ live** | 700 | 0.700 | 0.640 | 0.334 | **+0.060, 4.66σ** | better than the prior |
| preflop, 2 live (M251's cell) | 300 | 0.418 | 0.385 | 0.341 | +0.034, 1.62σ | not separable |

**Multiway postflop is the one cell below uniform.** Placing equal weight
on every legal action would have agreed with Pluribus more than our
advice did.

Log loss is worse than the prior in **every** cell, including the
control, because our rows are near-pure and sometimes put about 0 on
what Pluribus sampled. It is a secondary score, and it punishes
confidence rather than direction. It does not change any reading.

### Where it lives: we bet far too often multiway

Mean frequencies:

| spot | Pluribus | this engine |
|---|---|---|
| multiway, checked to / first in (444) | bets **19%** | bets **59%** |
| — flop / turn / river | 18% / 21% / 22% | 57% / 60% / 66% |
| multiway, facing a bet (135) | folds 59%, calls 30%, raises 11% | folds 39%, calls 22%, **raises 39%** |
| heads-up, checked to / first in (507) | bets 36% | bets 54% |
| heads-up, facing a bet (192) | folds 53%, calls 39%, raises 8% | folds 28%, calls 52%, raises 20% |

**The ORDER is right and the LEVEL is wrong.** Checked to, by hero's hand
strength percentile:

| strength | multiway: we bet | Pluribus bets | heads-up: we bet | Pluribus bets |
|---|---|---|---|---|
| 0.00–0.50 | **48%** | 14% | 34% | 32% |
| 0.50–0.80 | **56%** | 14% | 54% | 30% |
| 0.80–0.95 | 75% | 30% | 77% | 42% |
| 0.95–1.00 | 81% | 43% | 67% | 60% |

- **Correlation with strength:** our bet share rises with strength
  (corr +0.26 multiway, +0.42 heads-up), so the advice does read the
  cards.
- **Multiway, the level is wrong:** with the weaker half of hands we bet
  **3.4× as often** as Pluribus.
- **Heads-up, the weak half is right:** there we match Pluribus. The
  excess is in middling hands, which fits the turn/river over-betting
  M222–M260 measured.

(A first pass read "our bet share is the same whether Pluribus checked or
bet" (0.589 against 0.584) as "no card information". That was one
sampled action's noise. The strength split is the right test, and it
says otherwise.)

### M254's gate, validated from outside

| multiway rows | n | ours | prior |
|---|---|---|---|
| gate says **stable** | 123 | **0.597** | 0.628 |
| gate says **split** | 456 | 0.406 | 0.631 |

- **The result:** stable minus split is **+0.19 at 4.26σ**, and stable
  rows sit level with the prior (−0.03, 0.73σ).
- **What that means:** the reproducibility gate M254 built from internal
  seed variance also separates the advice an outside expert agrees with.
- **What changed:** nothing. The gate is right as it is.

### What changed in the product

**`MULTIWAY_BET_NOTE` (`multiway-bet`) is new.**

- **When it fires:**
  - a 6-max table;
  - 3+ players live postflop;
  - node SPR ≥ 1.5 (every measured decision sat at 1.23 or more, median
    13.5);
  - hero's row at least 50% on bets and raises.
- **What its gate isolates:**
  - **Checked to:** it fires on 280 of 444. There Pluribus bet 18.9%, and
    our probability on its action was **0.31 against 0.68 where
    silent**.
  - **Facing a bet:** it fires on 46 of 135. There Pluribus raised 19.6%,
    called 54.3% and folded 26.1%.
- **What it says:** the frequencies, and that they come from one strong
  player and are not a price. Shipped text does not name the reference.
- **Tests:** four new tests pin the gate conditions and the copy.
- **Stone law:** the test now also forbids the hand store and its source
  by name.

### Two defects the replay found on the way

1. **`/advise` returned 200 with `strategy: null` for a real hand.**
   - **The cause:** out-of-range heroes are force-included as ONE combo,
     but the path caches were keyed by CLASS. A second combo of the same
     class hit the first one's solve and found no row. JcKc then KdJd on
     a 3-way flop reproduced it exactly.
   - **The fix:** combo-level paths are now keyed by the concrete combo;
     only the canonical library flop keeps class keys, because it
     includes the whole class.
   - **Test:** fails without the fix, passes with it.
   - **Why no benchmark caught it:** none ever flagged a missing hero
     row.
2. **`bench.hand_db.connect` left a write transaction open.** Any open
   store locked every other process out. It is fixed and tested.

### What this does not establish

- **That Pluribus is right.** It is a strong agent at 6-max, 100 bb, with
  its own sizes and against its own opponents.
- **A price in bb.** One sampled action has no EV.
- **Anything about 3-max, 9-max or other depths.** The note is gated off
  there.
- **Why the multiway engine over-bets.** The obvious candidates are
  already on file:
  - F46's sampling noise;
  - the uniform-reach node training (M150/M163);
  - range caps of 8 classes a position.

  None is tested here.
