import random
import threading
import time

import numpy as np
import pytest

from poker_solver.cards import Card
from poker_solver.equity import (
    MultiwayEquityCache,
    _pairwise_fallback_equity,
    _provably_infeasible,
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
# M33: deal_n_hands's optional rng parameter — docs/full-table-diagnostic-
# 2026-08.md's §3.5 (every suited hand in a multiway showdown was always
# dealt the same two suits, since _suit_pairs_for's fixed first-fit order
# means clubs — or whichever suit is first in SUITS — always wins when
# available).
# ---------------------------------------------------------------------------


def test_deal_n_hands_without_rng_always_deals_the_same_suit_to_suited_hands():
    # Pins the PRE-fix behavior explicitly (not just "every other test
    # passes unchanged") — the exact bias §3.5 identified, confirmed here
    # as the documented default, not an accident nobody verified.
    hands = [StartingHand(a, b, suited=True) for a, b in [("A", "K"), ("Q", "J"), ("T", "9")]]
    suits_seen = {deal_n_hands(hands)[0][0].suit for _ in range(50)}
    assert suits_seen == {"c"}


def test_deal_n_hands_with_rng_varies_which_suit_suited_hands_get():
    hands = [StartingHand(a, b, suited=True) for a, b in [("A", "K"), ("Q", "J"), ("T", "9")]]
    rng = random.Random(1)
    suits_seen = {deal_n_hands(hands, rng=rng)[0][0].suit for _ in range(200)}
    assert len(suits_seen) > 1  # no longer collapses onto a single suit


def test_deal_n_hands_with_rng_still_returns_distinct_valid_cards():
    # The shuffle changes WHICH suit-pair candidates get tried and in
    # what order — it must not affect the underlying correctness
    # guarantees (distinctness, suited/offsuit/pair respected).
    hands = [
        StartingHand("A", "K", suited=True),
        StartingHand("Q", "J", suited=False),
        StartingHand("2", "2"),
    ]
    rng = random.Random(3)
    for _ in range(100):
        (a1, a2), (b1, b2), (c1, c2) = deal_n_hands(hands, rng=rng)
        assert a1.suit == a2.suit
        assert b1.suit != b2.suit
        assert c1.suit != c2.suit
        assert len({a1, a2, b1, b2, c1, c2}) == 6


def test_deal_n_hands_with_rng_is_deterministic_given_the_rng_seed():
    hands = [StartingHand(a, b, suited=True) for a, b in [("A", "K"), ("Q", "J"), ("T", "9")]]
    first = deal_n_hands(hands, rng=random.Random(9))
    second = deal_n_hands(hands, rng=random.Random(9))
    assert first == second


def test_deal_n_hands_rng_defaults_to_none_matching_omitting_it_entirely():
    hands = [StartingHand(a, b, suited=True) for a, b in [("A", "K"), ("Q", "J"), ("T", "9")]]
    omitted = deal_n_hands(hands)
    explicit_none = deal_n_hands(hands, rng=None)
    assert omitted == explicit_none


# ---------------------------------------------------------------------------
# _provably_infeasible (M27's O(N) precheck ahead of deal_n_hands's
# exponential backtracking)
# ---------------------------------------------------------------------------


def test_provably_infeasible_true_when_a_rank_is_overcommitted():
    # 3 separate KK opponents demand 6 kings; only 4 exist.
    hands = [StartingHand("K", "K")] * 3
    assert _provably_infeasible(hands, frozenset()) is True


def test_provably_infeasible_false_when_ranks_are_within_supply():
    hands = [StartingHand("A", "A"), StartingHand("K", "K"), StartingHand("Q", "Q")]
    assert _provably_infeasible(hands, frozenset()) is False


def test_provably_infeasible_accounts_for_avoiding():
    # 2 AA hands need 2 aces — fine on an empty deck, but not once
    # `avoiding` already claims 3 of the 4 aces.
    hands = [StartingHand("A", "A"), StartingHand("A", "A")]
    assert _provably_infeasible(hands, frozenset()) is False
    avoiding = frozenset({Card("A", "s"), Card("A", "h"), Card("A", "d")})
    assert _provably_infeasible(hands, avoiding) is True


def test_provably_infeasible_is_necessary_not_sufficient():
    # A concrete suit-only conflict: rank counts are fine (1 ace needed,
    # 1 left; 1 king needed, 3 left), so the precheck must NOT flag it —
    # but the only remaining ace is the spade, and the spade king is
    # exactly the one that's gone, so a same-suited A-K hand still can't
    # actually be dealt. The precheck correctly declines to shortcut
    # this; deal_n_hands must still catch it via the backtracking
    # fallback, at full cost, exactly as if the precheck didn't exist.
    hand = StartingHand("A", "K", suited=True)
    avoiding = frozenset({Card("A", "h"), Card("A", "d"), Card("A", "c"), Card("K", "s")})
    assert _provably_infeasible([hand], avoiding) is False
    with pytest.raises(RuntimeError):
        deal_n_hands([hand], avoiding=avoiding)


def test_deal_n_hands_precheck_is_fast_for_a_confirmed_infeasible_case():
    # A real 8-hand case drawn the same weighted-random way MCCFR
    # actually samples opponents (found via a fixed-seed scratch sweep
    # during M27's design, not hand-crafted): rank 6 is demanded by 5
    # of these 8 hands (96o, A6o, K6o, 63s, 62o), 1 more than the 4
    # sixes that exist. Measured directly against a standalone copy of
    # the pre-M27 backtracking (no precheck): 1509.8ms. This test
    # doesn't need to reproduce that "before" number — it only needs
    # this bound to be one the old code would have blown through, which
    # 1 second is.
    hands = [
        StartingHand("9", "6", suited=False),
        StartingHand("A", "6", suited=False),
        StartingHand("K", "6", suited=False),
        StartingHand("T", "2", suited=False),
        StartingHand("9", "7", suited=False),
        StartingHand("6", "3", suited=True),
        StartingHand("6", "2", suited=False),
        StartingHand("Q", "Q", suited=False),
    ]
    start = time.perf_counter()
    with pytest.raises(RuntimeError):
        deal_n_hands(hands)
    assert time.perf_counter() - start < 1.0


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


def test_monte_carlo_equity_n_threads_rng_into_dealing_for_suit_diversity(monkeypatch):
    # M33: monte_carlo_equity_n already accepted an rng parameter, but
    # only ever used it for the runout sampling — it never reached
    # deal_n_hands itself, so simultaneously-suited hands still collapsed
    # onto the same suit (§3.5) even when a caller supplied their own rng.
    # Verified directly (not inferred): monte_carlo_equity_n's own call to
    # deal_n_hands passes rng, spied on via monkeypatch.
    import poker_solver.equity as equity_module

    calls = []
    real_deal_n_hands = equity_module.deal_n_hands

    def spy(hands, avoiding=frozenset(), rng=None):
        calls.append(rng)
        return real_deal_n_hands(hands, avoiding=avoiding, rng=rng)

    monkeypatch.setattr(equity_module, "deal_n_hands", spy)
    hands = [StartingHand("A", "K", suited=True), StartingHand("Q", "J", suited=True)]
    rng = random.Random(11)
    equity_module.monte_carlo_equity_n(hands, samples=5, rng=rng)
    assert len(calls) == 1
    assert calls[0] is rng


def test_monte_carlo_equity_n_matches_pairwise_for_two_hands():
    # With exactly 2 hands, monte_carlo_equity_n's tie/win logic reduces
    # to the pairwise case — approximately, not bit-for-bit, as of M33
    # (docs/full-table-diagnostic-2026-08.md's §3.5): monte_carlo_equity_n
    # now threads its own rng into deal_n_hands itself (so simultaneously-
    # suited hands don't all collapse onto the same suit), which consumes
    # rng draws deal_two_hands (used by the plain pairwise monte_carlo_
    # equity, which has no rng-shuffle to thread) never did — so the same
    # seed no longer drives an *identical* sequence of board draws between
    # the two, just an equally-valid one. Loosened from exact equality to
    # a generous Monte Carlo tolerance (200 samples, a ~2-way matchup) —
    # this test's original exact-match expectation was an artifact of
    # deal_n_hands/deal_two_hands happening to consume rng identically
    # pre-fix, not a property either function's own contract ever
    # promised.
    hand_a, hand_b = StartingHand("A", "K", suited=True), StartingHand("Q", "Q")
    n_result = monte_carlo_equity_n([hand_a, hand_b], samples=200, rng=random.Random(42))
    pairwise_result = monte_carlo_equity(hand_a, hand_b, samples=200, rng=random.Random(42))
    assert n_result[0] == pytest.approx(pairwise_result, abs=0.1)
    assert n_result[1] == pytest.approx(1.0 - pairwise_result, abs=0.1)


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
# _pairwise_fallback_equity
# ---------------------------------------------------------------------------


def test_pairwise_fallback_equity_is_a_probability():
    value = _pairwise_fallback_equity(StartingHand("A", "A"), (StartingHand("K", "K"),), random.Random(1))
    assert 0.0 <= value <= 1.0


def test_pairwise_fallback_equity_is_deterministic_given_a_seed():
    hand, opponents = StartingHand("A", "K", suited=True), (StartingHand("Q", "Q"), StartingHand("7", "2", suited=False))
    first = _pairwise_fallback_equity(hand, opponents, random.Random(7))
    second = _pairwise_fallback_equity(hand, opponents, random.Random(7))
    assert first == second


def test_pairwise_fallback_equity_matches_monte_carlo_equity_for_one_opponent():
    # mean() of a length-1 list is just that element — this fallback
    # should reduce to a plain pairwise call for a single opponent,
    # given the same rng state either way.
    hand, opponent = StartingHand("A", "A"), StartingHand("K", "K")
    fallback = _pairwise_fallback_equity(hand, (opponent,), random.Random(3))
    direct = monte_carlo_equity(hand, opponent, samples=50, rng=random.Random(3))
    assert fallback == pytest.approx(direct)


def test_pairwise_fallback_equity_reflects_relative_strength():
    # The whole point of this fallback (replacing M27's first, flatter
    # 1/n_live attempt): a strong hand should get a meaningfully higher
    # fallback value than a weak one against the exact same opponents.
    opponents = (StartingHand("K", "K"), StartingHand("Q", "Q"))
    strong = _pairwise_fallback_equity(StartingHand("A", "A"), opponents, random.Random(11))
    weak = _pairwise_fallback_equity(StartingHand("3", "2", suited=False), opponents, random.Random(11))
    assert strong > weak


def test_pairwise_fallback_equity_averages_across_multiple_opponents():
    # Against one strong (KK) and one weak (32o) opponent, the fallback
    # (only FALLBACK_PAIRWISE_SAMPLES=50 samples per matchup, deliberately
    # cheap — see its own comment) should land roughly between AA's TRUE
    # equity against each one alone, computed here at a much larger
    # sample count for a stable ground truth — proving it's actually
    # averaging both opponents, not just reflecting one of them, without
    # the test itself being sensitive to 50-sample noise.
    hand = StartingHand("A", "A")
    kk, weak = StartingHand("K", "K"), StartingHand("3", "2", suited=False)
    true_vs_kk = monte_carlo_equity(hand, kk, samples=2000, rng=random.Random(100))
    true_vs_weak = monte_carlo_equity(hand, weak, samples=2000, rng=random.Random(101))
    combined = _pairwise_fallback_equity(hand, (kk, weak), random.Random(4))
    lo, hi = min(true_vs_kk, true_vs_weak), max(true_vs_kk, true_vs_weak)
    assert lo - 0.1 < combined < hi + 0.1


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


def test_multiway_cache_is_order_independent_across_fresh_caches():
    # M33 (docs/full-table-diagnostic-2026-08.md's §3.6): the test above
    # only proves the SAME cache instance's own sorted-key lookup hits the
    # same entry either way — trivially true from the cache key alone,
    # regardless of whether the underlying deal is order-independent. The
    # diagnostic's own confirmed reproduction needed TWO SEPARATE, fresh
    # caches (no shared cache-hit shortcut possible) to actually surface
    # the bug: (AKs, T9o, KK) vs. (KK, T9o, AKs), same seed, differed by
    # up to 0.0069 pre-fix. This is the direct regression test for that.
    hands = [StartingHand("A", "A"), StartingHand("K", "K"), StartingHand("Q", "Q")]
    opp_a = StartingHand("A", "K", suited=True)
    opp_b = StartingHand("T", "9", suited=False)
    opp_c = StartingHand("K", "K")
    cache_forward = MultiwayEquityCache(hands=hands, samples=200, seed=5)
    cache_reversed = MultiwayEquityCache(hands=hands, samples=200, seed=5)
    forward = cache_forward.traverser_equity_vector((opp_a, opp_b, opp_c))
    reversed_order = cache_reversed.traverser_equity_vector((opp_c, opp_b, opp_a))
    assert np.array_equal(forward, reversed_order)


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
    # for a candidate traverser hand is physically impossible even after
    # the joint-redeal retry (2 opponents + 1 candidate, all KK, need 6
    # kings). Must not crash; the impossible entry gets the hand-aware
    # pairwise fallback (see _pairwise_fallback_equity) — a KK-vs-KK
    # mirror match should land close to a real 0.5, not a flat n-way
    # split like the old 1/3 (M27's first, since-replaced attempt).
    cache = MultiwayEquityCache(hands=[StartingHand("K", "K"), StartingHand("A", "A")], samples=20)
    vector = cache.traverser_equity_vector((StartingHand("K", "K"), StartingHand("K", "K")))
    assert len(vector) == 2
    assert not np.any(np.isnan(vector))
    assert np.all(vector >= 0.0)
    assert np.all(vector <= 1.0)
    assert vector[0] > 0.4  # KK candidate: blocked, hand-aware fallback close to a real mirror-match 0.5
    assert vector[1] != pytest.approx(vector[0])  # AA candidate: not blocked, a real simulated value


def test_multiway_cache_blocked_candidate_placeholder_reflects_real_strength():
    # 4 live opponents (KK, QQ, AA, AA — the two AAs together use all 4
    # aces) block a 5th AA candidate even after the joint-redeal retry.
    # The old flat-1/n_live placeholder (1/5 = 0.2, M27's first, since-
    # replaced attempt) was blind to AA being the best possible hand;
    # the hand-aware pairwise fallback should instead land well above
    # that, close to AA's real average edge over this specific field.
    cache = MultiwayEquityCache(hands=[StartingHand("A", "A")], samples=20)
    opponents = (
        StartingHand("K", "K"),
        StartingHand("Q", "Q"),
        StartingHand("A", "A"),  # together with the next AA, uses all 4 aces
        StartingHand("A", "A"),
    )
    vector = cache.traverser_equity_vector(opponents)
    assert vector[0] > 0.5


def test_multiway_cache_opponents_mutually_infeasible_is_hand_aware():
    # 3 separate opponents all holding KK demand 6 kings — the opponent
    # tuple itself can't be dealt at all, hitting the OTHER fallback
    # branch (distinct from the blocked-candidate tests above, which
    # all have feasible opponents). The old flat-n-way-split placeholder
    # (M27's first, since-replaced attempt) gave every candidate the
    # exact same value regardless of hand strength; the hand-aware
    # pairwise fallback should instead clearly favor AA (dominates KK
    # pairwise) over 72o (dominated by KK pairwise) against this same
    # opponent tuple.
    cache = MultiwayEquityCache(hands=[StartingHand("A", "A"), StartingHand("7", "2", suited=False)], samples=20)
    opponents = (StartingHand("K", "K"), StartingHand("K", "K"), StartingHand("K", "K"))
    vector = cache.traverser_equity_vector(opponents)
    assert len(vector) == 2
    assert not np.any(np.isnan(vector))
    assert np.all(vector >= 0.0)
    assert np.all(vector <= 1.0)
    assert vector[0] > vector[1]  # AA's fallback clearly beats 72o's, same opponents either way
    assert vector[0] > 0.6  # AA vs KK pairwise is lopsided in AA's favor
    assert vector[1] < 0.4  # 72o vs KK pairwise is lopsided in KK's favor


def test_multiway_cache_deterministic_across_separate_caches_with_same_seed():
    hands = [StartingHand("A", "A"), StartingHand("7", "2", suited=False)]
    opponents = (StartingHand("K", "K"), StartingHand("Q", "Q"))
    cache1 = MultiwayEquityCache(hands=hands, samples=30, seed=99)
    cache2 = MultiwayEquityCache(hands=hands, samples=30, seed=99)
    assert np.array_equal(cache1.traverser_equity_vector(opponents), cache2.traverser_equity_vector(opponents))


# ---------------------------------------------------------------------------
# M34: thread safety (docs/full-table-diagnostic-2026-08.md's §3.10) —
# MultiwayEquityCache._cache's own lock, and get_equity_table's atomic
# on-disk write + lock. InfoSetTable's own §3.10 gap is deliberately
# documented rather than locked — see its class docstring in cfr.py.
# ---------------------------------------------------------------------------


def test_multiway_cache_concurrent_access_same_key_is_safe_and_consistent():
    # Many threads racing for the IDENTICAL opponent tuple must never
    # crash or corrupt self._cache — and since the computation is
    # deterministic given (seed, key), every thread's own result must be
    # bit-identical too, regardless of who "won" the cache write.
    hands = [StartingHand("A", "A"), StartingHand("K", "K"), StartingHand("Q", "Q")]
    opponents = (StartingHand("7", "2", suited=False), StartingHand("8", "3", suited=False))
    cache = MultiwayEquityCache(hands=hands, samples=30, seed=1)

    results = []
    errors = []

    def worker():
        try:
            results.append(cache.traverser_equity_vector(opponents))
        except Exception as exc:  # pragma: no cover - failure path only
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(results) == 16
    for result in results[1:]:
        assert np.array_equal(result, results[0])
    assert len(cache) == 1  # one distinct key was ever requested


def test_multiway_cache_concurrent_access_different_keys_all_correct():
    hands = [StartingHand("A", "A"), StartingHand("K", "K")]
    opponent_pool = [
        StartingHand("7", "2", suited=False), StartingHand("8", "3", suited=False),
        StartingHand("9", "4", suited=False), StartingHand("T", "5", suited=False),
        StartingHand("J", "6", suited=False), StartingHand("Q", "7", suited=False),
    ]
    cache = MultiwayEquityCache(hands=hands, samples=30, seed=2)
    opponent_tuples = [(opponent_pool[i], opponent_pool[i + 1]) for i in range(0, 6, 2)]

    results: dict = {}
    lock = threading.Lock()
    errors = []

    def worker(opponents):
        try:
            vector = cache.traverser_equity_vector(opponents)
            with lock:
                results[opponents] = vector
        except Exception as exc:  # pragma: no cover - failure path only
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(opponents,)) for opponents in opponent_tuples for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(cache) == 3  # exactly the 3 distinct opponent tuples requested
    for opponents in opponent_tuples:
        expected = cache.traverser_equity_vector(opponents)  # a guaranteed cache hit now
        assert np.array_equal(results[opponents], expected)


def test_get_equity_table_concurrent_cold_start_never_produces_a_corrupt_file(tmp_path):
    # The real, already-live race this milestone fixes: many threads all
    # reaching a nonexistent cache file at once (the pre-warm-thread-vs-
    # live-request shape CLAUDE.md's M14 entry already measured real
    # contention for). Every thread must get back a valid, correctly-
    # shaped table — never a crash from a torn/partial read — and the
    # file left on disk afterward must itself load cleanly.
    hands = [StartingHand("A", "A"), StartingHand("K", "K"), StartingHand("Q", "Q")]
    cache_path = tmp_path / "concurrent_equity.npy"
    assert not cache_path.exists()

    results = []
    errors = []

    def worker():
        try:
            results.append(get_equity_table(cache_path=cache_path, hands=hands, samples=20))
        except Exception as exc:  # pragma: no cover - failure path only
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(results) == 8
    for table in results:
        assert table.shape == (3, 3)
    assert cache_path.exists()
    # No leftover temp files from any thread that lost the race.
    leftover_tmp = list(tmp_path.glob("*.tmp-*"))
    assert leftover_tmp == []
    # The file actually on disk loads cleanly and matches what every
    # thread received — the direct proof no torn write ever landed.
    on_disk = np.load(cache_path)
    assert np.array_equal(on_disk, results[0])

