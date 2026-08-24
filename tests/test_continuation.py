"""Tests for poker_solver/continuation.py (M113).

One test module per source module, per this project's convention.
"""

import math

import numpy as np
import pytest

from poker_solver.cfr import solve
from poker_solver.continuation import expected_values_at_root
from poker_solver.game_tree import StreetConfig, build_street_tree
from poker_solver.solver import StrategyResult


def _solved(equity, pot, stack, iterations=300):
    num_hands = equity.shape[0]
    hands = [f"h{i}" for i in range(num_hands)]
    config = StreetConfig(positions=("OOP", "IP"), pot=pot, stack_bb=stack,
                          raise_sizes=(2.5,), max_raises=2)
    root = build_street_tree(config)
    reach = {"OOP": np.ones(num_hands), "IP": np.ones(num_hands)}
    node_data = solve(root, hands, equity, iterations=iterations,
                      positions=("OOP", "IP"), initial_reach=reach)
    return StrategyResult(config=config, root=root, hands=hands, node_data=node_data,
                          iterations=iterations, elapsed_seconds=0.0)


@pytest.mark.parametrize(
    "label,equity,pot,stack",
    [
        ("asymmetric", np.array([[0.5, 0.8], [0.2, 0.5]]), 10.0, 50.0),
        ("symmetric", np.array([[0.5, 0.5], [0.5, 0.5]]), 10.0, 50.0),
        ("three hands", np.array([[0.5, 0.7, 0.9], [0.3, 0.5, 0.75], [0.1, 0.25, 0.5]]),
         20.0, 40.0),
    ],
)
def test_both_sides_expected_values_sum_to_the_pot(label, equity, pot, stack):
    """The invariant that makes this quantity trustworthy without knowing
    the solution.

    Terminal payoffs are `equity * pot - invested`; equities sum to 1 and
    the street's own investments cancel, so both sides' expected values —
    each averaged over the other's range — must sum to exactly the pot.

    Asserted rather than a predicted number ON PURPOSE. The first attempt
    at validating this walker predicted "hand A is worth 6.5" from raw
    equity arithmetic, and was wrong because the tree offered an all-in
    and was never the check-down that assumed. An invariant needs no such
    guess.

    Note `1.0 - equity.T` for IP: IP's equity against OOP is one minus
    OOP's, transposed into (IP hand, OOP hand) orientation. Passing
    `equity.T` alone is OOP's equity relabelled, and produced a residual
    that looked like a real error in the walker.
    """
    result = _solved(equity, pot, stack)
    oop = expected_values_at_root(result, equity, "OOP", "IP")
    ip = expected_values_at_root(result, 1.0 - equity.T, "IP", "OOP")
    assert float(oop.mean() + ip.mean()) == pytest.approx(pot, abs=1e-6), (
        f"{label}: the two sides' EV does not account for the whole pot"
    )


def test_a_stronger_hand_is_worth_more_than_a_weaker_one():
    """Directional sanity: EV must be monotone in equity. A continuation
    value that ranked a dominated hand above a dominating one would poison
    every terminal it priced, while still summing to the pot."""
    equity = np.array([[0.5, 0.7, 0.9], [0.3, 0.5, 0.75], [0.1, 0.25, 0.5]])
    result = _solved(equity, 20.0, 40.0)
    values = expected_values_at_root(result, equity, "OOP", "IP")
    assert values[0] > values[1] > values[2], f"EV is not monotone in hand strength: {values}"


def test_villain_reach_is_normalized_and_actually_used():
    """A caller passing unnormalized weights must get the same answer as
    one passing normalized weights — and weighting villain toward strong
    hands must LOWER hero's EV, or the parameter is decoration."""
    equity = np.array([[0.5, 0.8], [0.2, 0.5]])
    result = _solved(equity, 10.0, 50.0)

    uniform = expected_values_at_root(result, equity, "OOP", "IP", villain_reach=[1.0, 1.0])
    scaled = expected_values_at_root(result, equity, "OOP", "IP", villain_reach=[7.0, 7.0])
    assert np.allclose(uniform, scaled), "unnormalized weights changed the answer"

    # h0 is villain's strong hand (hero's equity against it is 0.5, vs 0.8
    # against h1), so weighting villain onto h0 must cost hero EV.
    tilted = expected_values_at_root(result, equity, "OOP", "IP", villain_reach=[9.0, 1.0])
    assert tilted[0] < uniform[0], "villain_reach does not affect the result"


def test_an_empty_villain_range_is_refused():
    """Silently dividing by zero would produce NaN EVs that propagate into
    every terminal priced from them — the fabricated-value failure this
    codebase's `trained` flags exist to prevent."""
    equity = np.array([[0.5, 0.8], [0.2, 0.5]])
    result = _solved(equity, 10.0, 50.0)
    with pytest.raises(ValueError, match="sums to zero"):
        expected_values_at_root(result, equity, "OOP", "IP", villain_reach=[0.0, 0.0])


# --- M114: the canonical key and the precomputed table ---


def test_the_key_collapses_terminals_that_share_a_following_game():
    """M112's measurement is the reason this key exists: 6-max has 15,254
    showdown terminals with money behind, and one flop solve each is 8.3
    hours. They collapse to 27 because a continuation value depends on the
    game that FOLLOWS — pot, money behind, live seats — not on the action
    sequence that reached it.

    So two terminals with the same SPR and the same live-seat count must
    map to one key even when their pots and stacks differ in absolute
    size, and different SPRs must not collide.
    """
    from poker_solver.continuation import continuation_key

    # Same SPR (2.0), different absolute sizes -> one key.
    assert continuation_key(10.0, 20.0, 2) == continuation_key(40.0, 80.0, 2)
    # Same sizes, different live counts -> different keys.
    assert continuation_key(10.0, 20.0, 2) != continuation_key(10.0, 20.0, 3)
    # Order-of-magnitude apart in SPR -> different keys.
    assert continuation_key(10.0, 20.0, 2) != continuation_key(10.0, 160.0, 2)


def test_a_zero_pot_has_no_spr_and_is_refused():
    """SPR is undefined without a pot, and a silent divide would produce
    a key that silently merges unrelated spots."""
    from poker_solver.continuation import continuation_key

    with pytest.raises(ValueError, match="pot must be positive"):
        continuation_key(0.0, 50.0, 2)


def test_the_table_ranks_hands_and_responds_to_stack_depth():
    """The built table has to carry real postflop structure, or it cannot
    fix anything it is wired into.

    Two properties, both consequences of solved play rather than anything
    programmed in:
      * a premium hand is worth more than trash;
      * a premium hand is worth MORE at low SPR, because deep stacks give
        opponents room to outplay it, while trash is worth LESS at low
        SPR because it has less room to bluff.
    """
    from poker_solver.continuation import build_continuation_table, continuation_key
    from poker_solver.starting_hands import StartingHand as Hand

    hero = {Hand("A", "A"): 1.0, Hand("K", "Q", suited=True): 1.0,
            Hand("7", "2", suited=False): 1.0, Hand("J", "T", suited=True): 1.0}
    deep_key = continuation_key(10.0, 95.0, 2)
    shallow_key = continuation_key(40.0, 60.0, 2)
    table = build_continuation_table(
        {deep_key: (10.0, 95.0), shallow_key: (40.0, 60.0)},
        hero, dict(hero), boards=2, iterations=150, seed=3,
    )

    for key in (deep_key, shallow_key):
        values = table[key]
        assert values["AA"] > values["72o"], f"{key}: AA is not worth more than 72o"
        assert all(math.isfinite(v) for v in values.values()), f"{key}: non-finite EV"

    assert table[shallow_key]["AA"] > table[deep_key]["AA"], (
        "AA should realize more of the pot at low SPR, where opponents have less "
        "room to outplay it"
    )
    assert table[shallow_key]["72o"] < table[deep_key]["72o"], (
        "trash should be worth less at low SPR, having less room to bluff"
    )
