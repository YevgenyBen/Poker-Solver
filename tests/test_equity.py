import random

import numpy as np
import pytest

from poker_solver.equity import (
    MultiwayEquityCache,
    _stable_seed,
    build_equity_table,
    deal_n_hands,
    deal_two_hands,
    get_equity_table,
    monte_carlo_equity,
    monte_carlo_equity_n,
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


# ---------------------------------------------------------------------------
# deal_n_hands
# ---------------------------------------------------------------------------


def test_deal_n_hands_returns_distinct_cards():
    hands = [StartingHand("A", "A"), StartingHand("K", "K"), StartingHand("Q", "Q")]
    dealt = deal_n_hands(hands)
    all_cards = [card for pair in dealt for card in pair]
    assert len(set(all_cards)) == 6


def test_deal_n_hands_respects_suited_and_offsuit_and_pair():
    hands = [
        StartingHand("A", "K", suited=True),
        StartingHand("Q", "J", suited=False),
        StartingHand("2", "2"),
    ]
    (a1, a2), (b1, b2), (c1, c2) = deal_n_hands(hands)
    assert a1.suit == a2.suit
    assert b1.suit != b2.suit
    assert c1.suit != c2.suit


def test_deal_n_hands_handles_overlapping_ranks_across_many_hands():
    hands = [
        StartingHand("A", "K", suited=True),
        StartingHand("A", "Q", suited=False),
        StartingHand("A", "J", suited=True),
    ]
    dealt = deal_n_hands(hands)
    all_cards = [card for pair in dealt for card in pair]
    assert len(set(all_cards)) == 6


def test_deal_n_hands_works_for_larger_n():
    hands = [StartingHand(rank, rank) for rank in ["A", "K", "Q", "J", "T", "9"]]
    dealt = deal_n_hands(hands)
    all_cards = [card for pair in dealt for card in pair]
    assert len(set(all_cards)) == 12


def test_deal_n_hands_matches_deal_two_hands_for_two_hands():
    hand_a, hand_b = StartingHand("A", "K", suited=True), StartingHand("A", "Q", suited=False)
    assert deal_n_hands([hand_a, hand_b]) == list(deal_two_hands(hand_a, hand_b))


# ---------------------------------------------------------------------------
# monte_carlo_equity_n
# ---------------------------------------------------------------------------


def test_monte_carlo_equity_n_sums_to_one():
    hands = [StartingHand("A", "A"), StartingHand("K", "K"), StartingHand("7", "2", suited=False)]
    shares = monte_carlo_equity_n(hands, samples=100)
    assert sum(shares) == pytest.approx(1.0)


def test_monte_carlo_equity_n_matches_length_and_order():
    hands = [StartingHand("A", "A"), StartingHand("K", "K")]
    assert len(monte_carlo_equity_n(hands, samples=50)) == 2


def test_monte_carlo_equity_n_aa_dominates_two_weak_hands():
    hands = [
        StartingHand("A", "A"),
        StartingHand("7", "2", suited=False),
        StartingHand("8", "3", suited=False),
    ]
    shares = monte_carlo_equity_n(hands, samples=300, rng=random.Random(1))
    assert shares[0] > 0.6
    assert shares[0] > shares[1]
    assert shares[0] > shares[2]


def test_monte_carlo_equity_n_is_deterministic_given_a_seed():
    hands = [StartingHand("A", "K", suited=True), StartingHand("Q", "Q"), StartingHand("7", "2", suited=False)]
    first = monte_carlo_equity_n(hands, samples=50, rng=random.Random(5))
    second = monte_carlo_equity_n(hands, samples=50, rng=random.Random(5))
    assert first == second


def test_monte_carlo_equity_n_matches_pairwise_for_two_hands():
    # With exactly 2 hands, monte_carlo_equity_n's tie/win logic reduces
    # to the pairwise case exactly (deal_n_hands agrees with
    # deal_two_hands for 2 hands, so the same rng seed drives an
    # identical sequence of board draws).
    hand_a, hand_b = StartingHand("A", "K", suited=True), StartingHand("Q", "Q")
    n_result = monte_carlo_equity_n([hand_a, hand_b], samples=200, rng=random.Random(42))
    pairwise_result = monte_carlo_equity(hand_a, hand_b, samples=200, rng=random.Random(42))
    assert n_result[0] == pytest.approx(pairwise_result)
    assert n_result[1] == pytest.approx(1.0 - pairwise_result)


# ---------------------------------------------------------------------------
# _stable_seed
# ---------------------------------------------------------------------------


def test_stable_seed_is_deterministic():
    assert _stable_seed(42, "AA", "KK") == _stable_seed(42, "AA", "KK")


def test_stable_seed_differs_for_different_inputs():
    assert _stable_seed(42, "AA", "KK") != _stable_seed(42, "AA", "QQ")


def test_stable_seed_differs_for_different_master_seed():
    assert _stable_seed(1, "AA", "KK") != _stable_seed(2, "AA", "KK")


# ---------------------------------------------------------------------------
# MultiwayEquityCache
# ---------------------------------------------------------------------------


def test_multiway_cache_starts_empty():
    cache = MultiwayEquityCache(hands=[StartingHand("A", "A"), StartingHand("K", "K")], samples=20)
    assert len(cache) == 0


def test_multiway_cache_populates_on_first_touch():
    cache = MultiwayEquityCache(hands=[StartingHand("A", "A"), StartingHand("K", "K")], samples=20)
    cache.traverser_equity_vector((StartingHand("7", "2", suited=False), StartingHand("8", "3", suited=False)))
    assert len(cache) == 1


def test_multiway_cache_hit_returns_identical_vector_without_growing():
    cache = MultiwayEquityCache(hands=[StartingHand("A", "A"), StartingHand("K", "K")], samples=20)
    opponents = (StartingHand("7", "2", suited=False), StartingHand("8", "3", suited=False))
    first = cache.traverser_equity_vector(opponents)
    second = cache.traverser_equity_vector(opponents)
    assert np.array_equal(first, second)
    assert len(cache) == 1


def test_multiway_cache_is_order_independent_for_opponent_tuple():
    cache = MultiwayEquityCache(hands=[StartingHand("A", "A")], samples=20)
    opp_a = StartingHand("7", "2", suited=False)
    opp_b = StartingHand("8", "3", suited=False)
    forward = cache.traverser_equity_vector((opp_a, opp_b))
    reversed_order = cache.traverser_equity_vector((opp_b, opp_a))
    assert np.array_equal(forward, reversed_order)
    assert len(cache) == 1  # both requests hit the same canonical entry


def test_multiway_cache_vector_length_matches_hands():
    hands = [StartingHand("A", "A"), StartingHand("K", "K"), StartingHand("Q", "Q")]
    cache = MultiwayEquityCache(hands=hands, samples=20)
    vector = cache.traverser_equity_vector(
        (StartingHand("7", "2", suited=False), StartingHand("8", "3", suited=False))
    )
    assert len(vector) == 3


def test_multiway_cache_values_are_probabilities():
    cache = MultiwayEquityCache(hands=[StartingHand("A", "A"), StartingHand("7", "2", suited=False)], samples=20)
    vector = cache.traverser_equity_vector((StartingHand("K", "K"), StartingHand("Q", "Q")))
    assert np.all(vector >= 0.0)
    assert np.all(vector <= 1.0)


def test_multiway_cache_handles_blocked_traverser_hand_gracefully():
    # Both opponents hold KK — that's all 4 kings gone, so a third KK
    # for a candidate traverser hand is physically impossible. Must not
    # crash; the impossible entry gets a neutral placeholder since its
    # true probability is 0 regardless of the value assigned.
    cache = MultiwayEquityCache(hands=[StartingHand("K", "K"), StartingHand("A", "A")], samples=20)
    vector = cache.traverser_equity_vector((StartingHand("K", "K"), StartingHand("K", "K")))
    assert len(vector) == 2
    assert not np.any(np.isnan(vector))
    assert np.all(vector >= 0.0)
    assert np.all(vector <= 1.0)


def test_multiway_cache_deterministic_across_separate_caches_with_same_seed():
    hands = [StartingHand("A", "A"), StartingHand("7", "2", suited=False)]
    opponents = (StartingHand("K", "K"), StartingHand("Q", "Q"))
    cache1 = MultiwayEquityCache(hands=hands, samples=30, seed=99)
    cache2 = MultiwayEquityCache(hands=hands, samples=30, seed=99)
    assert np.array_equal(cache1.traverser_equity_vector(opponents), cache2.traverser_equity_vector(opponents))

