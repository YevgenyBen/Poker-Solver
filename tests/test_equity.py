import itertools
import random
import threading
import time

import numpy as np
import pytest

from poker_solver.cards import _ALL_CARDS, Card
from poker_solver.equity import (
    _HAND_TABLE_INDEX,
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
from poker_solver.hand_eval import best_hand_rank_batch
from poker_solver.starting_hands import StartingHand, all_starting_hands


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

    # M191: the spy records the CALLING THREAD, and only this thread's
    # calls are counted. Monkeypatching a module attribute is
    # process-wide, so a background thread — an API prewarm left running
    # by an earlier test, for instance — lands its own calls in the same
    # list. That is exactly what happened: under the full suite this
    # collected 171 calls where the test makes 1, while passing whenever
    # the file ran alone or the prewarm had finished first.
    #
    # The flaw was in the test, not the product, and it went unnoticed
    # until M190's wider range caps made prewarm slow enough to still be
    # running here. Counting a global call total was never what this test
    # meant to assert.
    import threading

    calls = []
    real_deal_n_hands = equity_module.deal_n_hands
    this_thread = threading.get_ident()

    def spy(hands, avoiding=frozenset(), rng=None):
        if threading.get_ident() == this_thread:
            calls.append(rng)
        return real_deal_n_hands(hands, avoiding=avoiding, rng=rng)

    monkeypatch.setattr(equity_module, "deal_n_hands", spy)
    hands = [StartingHand("A", "K", suited=True), StartingHand("Q", "J", suited=True)]
    rng = random.Random(11)
    equity_module.monte_carlo_equity_n(hands, samples=5, rng=rng)
    assert len(calls) == 1, (
        f"expected one call from this thread, got {len(calls)} — if this is "
        "again picking up other threads, the guard above has stopped working")
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


def test_pairwise_fallback_equity_reduces_to_the_pairwise_value_for_one_opponent():
    # mean() of a length-1 list is just that element — this fallback
    # should reduce to a plain pairwise equity for a single opponent.
    #
    # M70 changed WHERE that pairwise number comes from: the precomputed
    # 169x169 table rather than a fresh 50-sample Monte Carlo. So this
    # asserts the contract (it is that hand's pairwise equity) instead of
    # the old implementation detail (it is that specific MC call with
    # that specific rng state). The table is built at 200 samples, so it
    # is the more precise estimate of the two; a fresh 50-sample run is
    # only required to agree within its own sampling noise.
    hand, opponent = StartingHand("A", "A"), StartingHand("K", "K")
    fallback = _pairwise_fallback_equity(hand, (opponent,), random.Random(3))
    table = get_equity_table()
    expected = table[_HAND_TABLE_INDEX[str(hand)], _HAND_TABLE_INDEX[str(opponent)]]
    assert fallback == pytest.approx(expected)

    noisy = monte_carlo_equity(hand, opponent, samples=50, rng=random.Random(3))
    assert fallback == pytest.approx(noisy, abs=0.12)


def test_pairwise_fallback_equity_consumes_no_randomness():
    """M70: reading the table instead of simulating means the fallback no
    longer depends on rng state at all. That kills, by construction, the
    order-dependence M68 had to fix by sorting — and it means the amount
    of rng an upstream caller consumes can no longer perturb this value.
    """
    hand = StartingHand("A", "K", suited=True)
    opponents = (StartingHand("Q", "Q"), StartingHand("7", "2", suited=False))
    rng = random.Random(11)
    before = rng.getstate()
    first = _pairwise_fallback_equity(hand, opponents, rng)
    assert rng.getstate() == before, "the fallback must not advance the rng"

    # Same value from any rng state, and from any argument order.
    assert first == _pairwise_fallback_equity(hand, opponents, random.Random(999))
    assert first == pytest.approx(_pairwise_fallback_equity(hand, opponents[::-1], random.Random(4)))


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


def test_shared_board_equity_agrees_with_per_candidate_simulation():
    """M68 replaced a per-candidate simulation loop with one shared set of
    board runouts. It is a different estimator of the same quantity — not
    bit-identical and never can be — so it has to agree statistically with
    high-sample ground truth rather than exactly with the old code.

    Measured over a real 169-class pool: bias -0.0065, MAE 0.0494 against
    a 4,000-sample truth, which is what pure sampling noise looks like at
    samples=50 (SE ~0.053). The bounds here are deliberately loose enough
    to be about correctness, not to re-pin the noise.
    """
    hands = list(all_starting_hands())
    opponents = (
        StartingHand("K", "K"),
        StartingHand("7", "2", suited=False),
        StartingHand("9", "8", suited=True),
    )
    cache = MultiwayEquityCache(hands=hands, samples=200, seed=11)
    vector = cache.traverser_equity_vector(opponents)

    assert len(vector) == len(hands)
    finite = vector[~np.isnan(vector)]
    assert np.all(finite >= 0.0) and np.all(finite <= 1.0)

    index = {str(hand): i for i, hand in enumerate(hands)}
    # A dominant hand must beat a trash hand by a wide, unambiguous margin
    # — the property a broken shared-board mask would destroy first.
    assert vector[index["AA"]] > vector[index["32o"]] + 0.20
    # And the values must be in a sane multiway band, not heads-up-like:
    # against 3 opponents a strong hand cannot be worth 0.8 of the pot.
    assert 0.25 < vector[index["AA"]] < 0.75


def test_shared_board_equity_is_order_independent_for_the_same_opponents():
    """The same multiway situation described in two orders is one
    situation. M68 found this held only by luck — _pairwise_fallback_
    equity iterated the caller's order while the cache key was sorted, so
    (AKs, T9o, KK) and (KK, T9o, AKs) produced 0.690 and 0.700."""
    hands = [StartingHand("A", "A"), StartingHand("K", "K"), StartingHand("Q", "Q")]
    a = StartingHand("A", "K", suited=True)
    b = StartingHand("T", "9", suited=False)
    c = StartingHand("K", "K")
    forward = MultiwayEquityCache(hands=hands, samples=200, seed=5).traverser_equity_vector((a, b, c))
    reverse = MultiwayEquityCache(hands=hands, samples=200, seed=5).traverser_equity_vector((c, b, a))
    assert np.array_equal(forward, reverse)


def test_multiway_validity_mask_flags_a_blocked_candidate():
    # Same setup as the KK-mirror test above: the KK candidate can't
    # physically coexist with two KK opponents, so its equity is a
    # fallback, while AA's is a real simulated value. traverser_equity_
    # vector has to return a float for both; the mask is what tells them
    # apart (M66), so cfr.py can decline to learn from the fabricated one.
    cache = MultiwayEquityCache(hands=[StartingHand("K", "K"), StartingHand("A", "A")], samples=20)
    opponents = (StartingHand("K", "K"), StartingHand("K", "K"))
    mask = cache.traverser_validity_mask(opponents)
    assert mask.dtype == bool
    assert len(mask) == 2
    assert mask[0] == False  # noqa: E712 - KK candidate: blocked, value is a fallback
    assert mask[1] == True   # noqa: E712 - AA candidate: a real simulated value


def test_multiway_validity_mask_is_all_true_when_nothing_is_blocked():
    cache = MultiwayEquityCache(
        hands=[StartingHand("A", "K", suited=True), StartingHand("7", "2", suited=False)],
        samples=20,
    )
    mask = cache.traverser_validity_mask((StartingHand("Q", "J", suited=False),))
    assert np.all(mask)


def test_multiway_validity_mask_is_all_false_when_opponents_conflict():
    # Three KK opponents need 6 kings, so no concrete deal exists at all
    # and EVERY candidate falls back — the whole-vector case, distinct
    # from the per-candidate one above.
    cache = MultiwayEquityCache(hands=[StartingHand("A", "A"), StartingHand("7", "2", suited=False)], samples=20)
    kk = StartingHand("K", "K")
    mask = cache.traverser_validity_mask((kk, kk, kk))
    assert not np.any(mask)


def test_multiway_validity_mask_matches_the_equity_vector_either_call_order():
    # The mask is filled in the same pass as the vector and cached under
    # the same key, so asking for either one first must give the same
    # answer — and asking for the second must not recompute.
    hands = [StartingHand("K", "K"), StartingHand("A", "A")]
    opponents = (StartingHand("K", "K"), StartingHand("K", "K"))

    mask_first = MultiwayEquityCache(hands=hands, samples=20, seed=3)
    mask_a = mask_first.traverser_validity_mask(opponents)
    vector_a = mask_first.traverser_equity_vector(opponents)

    vector_first = MultiwayEquityCache(hands=hands, samples=20, seed=3)
    vector_b = vector_first.traverser_equity_vector(opponents)
    mask_b = vector_first.traverser_validity_mask(opponents)

    assert np.array_equal(mask_a, mask_b)
    assert np.array_equal(vector_a, vector_b)
    assert len(mask_first) == 1  # one entry either way — no duplicate computation


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



# ---------------------------------------------------------------------------
# M106 — equity against ground truth this codebase can regenerate
# ---------------------------------------------------------------------------

# Exact all-in equities, computed by enumerating every one of the
# C(48,5) = 1,712,304 possible boards for the specific cards
# `deal_two_hands` assigns to each matchup. Reproduce with
# `_exact_equity_by_enumeration` below (~43s each, which is why the
# values are frozen here rather than recomputed every run).
#
# **Deliberately NOT taken from a published equity table (M106).** The
# audit that produced this test first checked against remembered
# published figures and got THREE of thirteen wrong — most glaringly
# `77 vs 65s`, entered as 0.606 when the true value is 0.818, because
# 0.606 is the figure for a pair facing OVERCARDS and 65s is entirely
# below a seven. Every one of those looked like an engine defect.
#
# Category frequencies (M105) are exact combinatorial facts and safe to
# quote from memory. Matchup equities are suit-configuration dependent
# and are not. Ground truth this repository can regenerate beats a number
# recalled from outside it.
_EXACT_EQUITY = {
    ("77", "65s"): 0.8184,
    ("AA", "KK"): 0.8264,
    ("AKs", "QJs"): 0.6595,
    ("A5s", "KQo"): 0.5995,
}


def _hand_from_label(label: str) -> StartingHand:
    if len(label) == 2:
        return StartingHand(label[0], label[1])
    return StartingHand(label[0], label[1], suited=label[2] == "s")


def _exact_equity_by_enumeration(hand_a: StartingHand, hand_b: StartingHand) -> float:
    """Ground truth: every board, no sampling. Kept so the frozen values
    above can be regenerated rather than trusted."""
    suit_index = {suit: index for index, suit in enumerate("cdhs")}
    (a1, a2), (b1, b2) = deal_two_hands(hand_a, hand_b)
    used = {a1, a2, b1, b2}
    deck = [card for card in _ALL_CARDS if card not in used]
    boards = np.array(list(itertools.combinations(range(len(deck)), 5)), dtype=np.int64)
    deck_values = np.array([c.value for c in deck], dtype=np.int64)
    deck_suits = np.array([suit_index[c.suit] for c in deck], dtype=np.int64)
    board_values, board_suits = deck_values[boards], deck_suits[boards]

    def ranks(first, second):
        values = np.concatenate([
            np.full((len(boards), 1), first.value),
            np.full((len(boards), 1), second.value),
            board_values,
        ], axis=1)
        suits = np.concatenate([
            np.full((len(boards), 1), suit_index[first.suit]),
            np.full((len(boards), 1), suit_index[second.suit]),
            board_suits,
        ], axis=1)
        return best_hand_rank_batch(values, suits)

    rank_a, rank_b = ranks(a1, a2), ranks(b1, b2)
    return float((np.sum(rank_a > rank_b) + 0.5 * np.sum(rank_a == rank_b)) / len(boards))


@pytest.mark.parametrize("labels,exact", sorted(_EXACT_EQUITY.items()))
def test_monte_carlo_equity_tracks_exact_enumeration(labels, exact):
    """The sampled estimator must converge on the enumerated truth.

    A 1.0pp tolerance at 25,000 samples is roughly 3 standard errors —
    and the seed is fixed, so this is deterministic rather than merely
    unlikely to flake — while still catching any *modelling* error
    (ignoring suitedness, mishandling ties, dealing the wrong number of
    board cards), which would move a matchup by far more than a
    percentage point.
    """
    hand_a, hand_b = (_hand_from_label(label) for label in labels)
    measured = monte_carlo_equity(hand_a, hand_b, samples=25_000, rng=random.Random(106))
    assert measured == pytest.approx(exact, abs=0.010), (
        f"{labels[0]} vs {labels[1]}: sampled {measured:.4f} against enumerated {exact:.4f}"
    )


def test_the_frozen_exact_values_can_still_be_regenerated():
    """Guards the constants above against drift in the thing that
    produced them. If `deal_two_hands` ever assigns different concrete
    cards, or the evaluator changes, the frozen numbers silently stop
    describing what the enumeration returns — and every test using them
    keeps passing against a stale truth.

    Only one matchup, because enumeration costs ~40s each.
    """
    exact = _exact_equity_by_enumeration(_hand_from_label("AA"), _hand_from_label("KK"))
    assert exact == pytest.approx(_EXACT_EQUITY[("AA", "KK")], abs=0.0001)


def test_equity_is_symmetric_within_sampling_error():
    """`equity(a, b) + equity(b, a)` must be 1. A tie-handling bug shows
    up here and in no single matchup's absolute value.

    Tolerated to 1pp rather than exactly, because the two directions deal
    their own concrete cards and therefore sample independently — an
    exact assertion here would be measuring the RNG, not the property.
    """
    for labels in _EXACT_EQUITY:
        hand_a, hand_b = (_hand_from_label(label) for label in labels)
        forward = monte_carlo_equity(hand_a, hand_b, samples=20_000, rng=random.Random(11))
        reverse = monte_carlo_equity(hand_b, hand_a, samples=20_000, rng=random.Random(11))
        assert forward + reverse == pytest.approx(1.0, abs=0.010), (
            f"{labels}: {forward:.4f} + {reverse:.4f} = {forward + reverse:.4f}"
        )
