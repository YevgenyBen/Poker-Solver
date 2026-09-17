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

*(Results follow.)*
