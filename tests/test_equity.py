import random

import numpy as np

from poker_solver.equity import (
    build_equity_table,
    deal_two_hands,
    get_equity_table,
    monte_carlo_equity,
)
from poker_solver.starting_hands import StartingHand


def test_deal_two_hands_returns_four_distinct_cards():
    (a1, a2), (b1, b2) = deal_two_hands(
        StartingHand("A", "K", suited=True), StartingHand("A", "Q", suited=False)
    )
    assert len({a1, a2, b1, b2}) == 4


def test_deal_two_hands_respects_suited_flag():
    (a1, a2), _ = deal_two_hands(StartingHand("A", "K", suited=True), StartingHand("2", "2"))
    assert a1.suit == a2.suit


def test_deal_two_hands_respects_offsuit_flag():
    (a1, a2), _ = deal_two_hands(StartingHand("A", "K", suited=False), StartingHand("2", "2"))
    assert a1.suit != a2.suit


def test_deal_two_hands_respects_pair():
    (a1, a2), _ = deal_two_hands(StartingHand("A", "A"), StartingHand("K", "K"))
    assert a1.rank == a2.rank == "A"
    assert a1.suit != a2.suit


def test_deal_two_hands_same_class_both_sides():
    # Both players holding the same class (e.g. AA vs AA, with different
    # physical aces) must still work.
    (a1, a2), (b1, b2) = deal_two_hands(StartingHand("A", "A"), StartingHand("A", "A"))
    assert len({a1, a2, b1, b2}) == 4


def test_monte_carlo_equity_is_a_probability():
    equity = monte_carlo_equity(
        StartingHand("A", "A"), StartingHand("7", "2", suited=False), samples=100
    )
    assert 0.0 <= equity <= 1.0


def test_monte_carlo_equity_is_deterministic_given_a_seed():
    hand_a, hand_b = StartingHand("A", "K", suited=True), StartingHand("Q", "Q")
    eq1 = monte_carlo_equity(hand_a, hand_b, samples=100, rng=random.Random(7))
    eq2 = monte_carlo_equity(hand_a, hand_b, samples=100, rng=random.Random(7))
    assert eq1 == eq2


def test_monte_carlo_equity_aa_dominates_72o():
    # Known reference: AA vs 72o is roughly 85-89% in AA's favor.
    # Bounds are wide to stay well clear of Monte Carlo noise at this
    # sample size while still catching a broken evaluator/simulation.
    equity = monte_carlo_equity(
        StartingHand("A", "A"),
        StartingHand("7", "2", suited=False),
        samples=800,
        rng=random.Random(1),
    )
    assert 0.75 <= equity <= 0.97


def test_monte_carlo_equity_aa_favored_over_kk():
    # Known reference: AA vs KK is roughly 80-82% in AA's favor.
    equity = monte_carlo_equity(
        StartingHand("A", "A"), StartingHand("K", "K"), samples=800, rng=random.Random(2)
    )
    assert 0.65 <= equity <= 0.95


def test_monte_carlo_equity_roughly_complementary():
    hand_a, hand_b = StartingHand("A", "K", suited=True), StartingHand("Q", "Q")
    eq_ab = monte_carlo_equity(hand_a, hand_b, samples=600, rng=random.Random(3))
    eq_ba = monte_carlo_equity(hand_b, hand_a, samples=600, rng=random.Random(4))
    assert abs((eq_ab + eq_ba) - 1.0) < 0.15


def test_build_equity_table_shape():
    hands = [StartingHand("A", "A"), StartingHand("K", "K"), StartingHand("2", "2")]
    table = build_equity_table(hands=hands, samples=50)
    assert table.shape == (3, 3)


def test_build_equity_table_exact_symmetry():
    hands = [
        StartingHand("A", "A"),
        StartingHand("A", "K", suited=True),
        StartingHand("7", "2", suited=False),
        StartingHand("K", "K"),
        StartingHand("Q", "Q"),
    ]
    table = build_equity_table(hands=hands, samples=50)
    assert np.allclose(table, 1.0 - table.T)


def test_build_equity_table_diagonal_is_half():
    hands = [StartingHand("A", "A"), StartingHand("K", "K")]
    table = build_equity_table(hands=hands, samples=50)
    assert np.all(table.diagonal() == 0.5)


def test_build_equity_table_values_in_bounds():
    hands = [StartingHand("A", "A"), StartingHand("7", "2", suited=False), StartingHand("K", "K")]
    table = build_equity_table(hands=hands, samples=50)
    assert np.all(table >= 0.0)
    assert np.all(table <= 1.0)


def test_get_equity_table_caches_to_disk(tmp_path):
    hands = [StartingHand("A", "A"), StartingHand("K", "K")]
    cache_path = tmp_path / "small_equity.npy"

    assert not cache_path.exists()
    first = get_equity_table(cache_path=cache_path, hands=hands, samples=50)
    assert cache_path.exists()

    second = get_equity_table(cache_path=cache_path, hands=hands, samples=50)
    assert np.array_equal(first, second)


def test_get_equity_table_force_rebuild_still_valid(tmp_path):
    hands = [StartingHand("A", "A"), StartingHand("K", "K")]
    cache_path = tmp_path / "small_equity.npy"

    get_equity_table(cache_path=cache_path, hands=hands, samples=50)
    rebuilt = get_equity_table(cache_path=cache_path, hands=hands, samples=50, force_rebuild=True)
    assert rebuilt.shape == (2, 2)
    assert np.all(rebuilt.diagonal() == 0.5)

