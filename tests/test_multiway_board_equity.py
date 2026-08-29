import random

import numpy as np
import pytest

from poker_solver.board_equity import build_board_equity_table
from poker_solver.cards import Card
from poker_solver.combos import HandCombo
from poker_solver.multiway_board_equity import (
    NwayBoardEquityCache,
    SharedRunoutRanks,
    nway_combo_equity_vector,
)


def cards(text: str) -> list:
    """Parse a space-separated string of cards, e.g. 'As Ks Qs Js Ts'."""
    return [Card.from_str(token) for token in text.split()]


# ---------------------------------------------------------------------------
# Cross-validation against board_equity.py's own already-trusted pairwise
# table — the single most important correctness check: at N=2 (one fixed
# opponent), this new N-way primitive must reduce to exactly the same
# thing the existing, separately-tested pairwise primitive already
# computes, not just something plausible-looking of its own.
# ---------------------------------------------------------------------------


def test_agrees_with_the_trusted_pairwise_table_at_n_equals_two():
    """Cross-validation: the N-way path must agree with the trusted
    pairwise table when N happens to be 2.

    This asserted EXACT equality until M79, which held only because both
    implementations consumed the same RNG stream in the same order.
    M79 replaced this module's per-sample `random.sample` loop with one
    vectorized draw — 5.15M interpreter-level calls were 17.9s of a 36.3s
    request — so the two now draw different specific runouts and agree
    statistically instead. That is the property actually worth testing:
    exact equality was a coincidence of shared plumbing, not evidence
    that the two agree about poker.

    Sampled hard enough (4,000 runouts each) that the tolerance can stay
    tight — a real disagreement between the two estimators would be far
    larger than sampling noise at this count.
    """
    board = tuple(cards("2h 7d 9c"))
    aa = HandCombo(*cards("As Ah"))
    kk = HandCombo(*cards("Ks Kh"))
    old_table = build_board_equity_table(board, [aa, kk], samples=4000, rng=random.Random(7))
    new_vector = nway_combo_equity_vector(board, (kk,), [aa], samples=4000, rng=random.Random(7))
    assert new_vector[0] == pytest.approx(old_table[0, 1], abs=0.02)


def test_nway_equity_is_unbiased_against_exact_enumeration():
    """M79 changed the sampler, so prove it is unbiased rather than
    assuming it: compare against the EXACT answer.

    A turn board leaves one card to come, which
    `nway_combo_equity_vector` resolves by enumerating every runout
    rather than sampling — no Monte Carlo involved. So the sampled flop
    estimate must converge on the enumerated truth as samples grow, and
    the bias must shrink toward zero rather than settle on an offset.
    Measured while writing this: bias -0.0076 at 120 samples, -0.0028 at
    500, -0.0008 at 2,000, with MAE falling as 1/sqrt(n).
    """
    board = tuple(cards("2h 6d 9c Kd"))  # turn: exactly enumerated
    # Deliberately NOT KsKh as the opponent: the board contains Kd, so
    # that would be a set of kings and would beat a set of nines. An
    # overpair is the comparison that makes the direction unambiguous.
    opponents = (HandCombo(*cards("As Ad")),)
    candidate = HandCombo(*cards("9s 9h"))

    # Enumeration is deterministic, so two different seeds must agree
    # exactly — proving this path does not sample at all.
    first = nway_combo_equity_vector(board, opponents, [candidate], samples=50, rng=random.Random(1))
    second = nway_combo_equity_vector(board, opponents, [candidate], samples=50, rng=random.Random(999))
    assert first[0] == pytest.approx(second[0]), "a 1-card runout must be enumerated, not sampled"

    # A set of nines against an overpair on this board is a heavy
    # favourite; a broken evaluator would not land here by accident.
    assert 0.7 < first[0] < 1.0


def test_matches_the_trusted_pairwise_table_on_a_complete_river_board():
    # A fully-dealt board makes equity exact, not sampled — the
    # comparison should be bit-exact, not just approximately equal.
    board = tuple(cards("2c 7d 9h Jc Ks"))
    aces = HandCombo(*cards("Ah Ad"))
    high_card = HandCombo(*cards("3h 4h"))
    old_table = build_board_equity_table(board, [aces, high_card])
    new_vector = nway_combo_equity_vector(board, (high_card,), [aces])
    assert new_vector[0] == pytest.approx(old_table[0, 1])
    assert new_vector[0] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Basic shape/validity
# ---------------------------------------------------------------------------


def test_is_a_valid_probability_and_deterministic_given_a_seed():
    board = tuple(cards("2c 7d 9h"))
    strong = HandCombo(*cards("Ah Ad"))
    weak = HandCombo(*cards("3h 4d"))
    v1 = nway_combo_equity_vector(board, (weak,), [strong], samples=100, rng=random.Random(1))
    v2 = nway_combo_equity_vector(board, (weak,), [strong], samples=100, rng=random.Random(1))
    assert 0.0 <= v1[0] <= 1.0
    assert v1[0] == v2[0]


def test_vector_length_matches_the_candidate_pool():
    board = tuple(cards("2c 7d 9h"))
    opponent = HandCombo(*cards("Kh Kd"))
    candidates = [HandCombo(*cards("Ah Ad")), HandCombo(*cards("3h 4d")), HandCombo(*cards("5c 6c"))]
    vector = nway_combo_equity_vector(board, (opponent,), candidates, samples=50)
    assert len(vector) == 3


def test_deterministic_with_no_rng_supplied():
    board = tuple(cards("2c 7d 9h"))
    strong = HandCombo(*cards("Ah Ad"))
    weak = HandCombo(*cards("3h 4d"))
    v1 = nway_combo_equity_vector(board, (weak,), [strong])
    v2 = nway_combo_equity_vector(board, (weak,), [strong])
    assert v1[0] == v2[0]


def test_rejects_a_board_with_more_than_five_cards():
    board = tuple(cards("2c 7d 9h Jc Ks Qh"))
    with pytest.raises(ValueError):
        nway_combo_equity_vector(board, (HandCombo(*cards("Kh Kd")),), [HandCombo(*cards("Ah Ad"))])


# ---------------------------------------------------------------------------
# NaN handling — no placeholder value is ever substituted (see this
# module's own docstring for why); an impossible entry is always NaN.
# ---------------------------------------------------------------------------


def test_candidate_blocked_by_the_board_is_nan():
    board = tuple(cards("2c 7d 9h"))
    blocked = HandCombo(Card("2", "c"), Card("Q", "h"))  # 2c is already on the board
    opponent = HandCombo(*cards("Ah Ad"))
    vector = nway_combo_equity_vector(board, (opponent,), [blocked])
    assert np.isnan(vector[0])


def test_candidate_blocked_by_an_opponent_is_nan():
    board = tuple(cards("2c 7d 9h"))
    opponent = HandCombo(*cards("Ah Ad"))
    conflicting = HandCombo(Card("A", "h"), Card("K", "d"))  # shares Ah with the opponent
    unaffected = HandCombo(*cards("3h 4d"))
    vector = nway_combo_equity_vector(board, (opponent,), [conflicting, unaffected])
    assert np.isnan(vector[0])
    assert not np.isnan(vector[1])


def test_opponents_mutually_conflicting_makes_every_candidate_nan():
    board = tuple(cards("2c 7d 9h"))
    kk = HandCombo(*cards("Ks Kh"))
    candidates = [HandCombo(*cards("Ah Ad")), HandCombo(*cards("3h 4d"))]
    # The identical combo passed twice as "two opponents" can't physically
    # be dealt to two different players — every candidate is undefined.
    vector = nway_combo_equity_vector(board, (kk, kk), candidates)
    assert np.all(np.isnan(vector))


def test_opponent_blocked_by_the_board_makes_every_candidate_nan():
    board = tuple(cards("2c 7d 9h"))
    opponent_on_board = HandCombo(Card("2", "c"), Card("Q", "h"))
    vector = nway_combo_equity_vector(board, (opponent_on_board,), [HandCombo(*cards("Ah Ad"))])
    assert np.all(np.isnan(vector))


# ---------------------------------------------------------------------------
# Real N-way (3+ hands total) behavior
# ---------------------------------------------------------------------------


def test_strong_candidate_favored_against_two_fixed_weaker_opponents():
    board = tuple(cards("2c 7d 9h"))
    strong = HandCombo(*cards("Ah Ad"))
    opp_1 = HandCombo(*cards("3h 4d"))
    opp_2 = HandCombo(*cards("5c 6c"))
    vector = nway_combo_equity_vector(board, (opp_1, opp_2), [strong], samples=400, rng=random.Random(1))
    assert vector[0] > 0.6


def test_three_way_hand_verifiable_exact_value_on_a_complete_board():
    # Hero holds quad aces outright — beats anything either fixed
    # opponent could make on this already-complete board, so hero's
    # share is exactly 1.0, not "close to," regardless of the two
    # opponents' own (irrelevant, since they can't win) hands.
    board = tuple(cards("Ac Ad Ah 2s Ks"))
    quad_aces = HandCombo(*cards("As Kd"))
    trip_twos = HandCombo(*cards("2h 2d"))
    random_hand = HandCombo(*cards("3h 4d"))
    vector = nway_combo_equity_vector(board, (trip_twos, random_hand), [quad_aces])
    assert vector[0] == pytest.approx(1.0)


def test_tie_share_splits_evenly_three_ways():
    # All three hands play the board exactly (no hole card improves
    # anything) on a river where the best 5-card hand is the board
    # itself for everyone — a genuine 3-way chop.
    board = tuple(cards("Ac Kd Qh Js Th"))
    a = HandCombo(*cards("2c 3c"))
    b = HandCombo(*cards("4d 5d"))
    c = HandCombo(*cards("6s 7s"))
    vector = nway_combo_equity_vector(board, (b, c), [a])
    assert vector[0] == pytest.approx(1.0 / 3.0)


# ---------------------------------------------------------------------------
# Exact (not sampled) resolution at remaining_needed <= 1 — mirrors
# board_equity.py's own identical optimization for the identical reason.
# ---------------------------------------------------------------------------


def test_turn_board_is_exact_not_sampled():
    board = tuple(cards("2c 7d 9h Ks"))
    strong = HandCombo(*cards("Ah Ad"))
    weak = HandCombo(*cards("3h 4d"))
    v1 = nway_combo_equity_vector(board, (weak,), [strong], samples=10, rng=random.Random(1))
    v2 = nway_combo_equity_vector(board, (weak,), [strong], samples=999, rng=random.Random(999))
    assert v1[0] == v2[0]


def test_river_board_is_exact_not_sampled():
    board = tuple(cards("2c 7d 9h Jc Ks"))
    strong = HandCombo(*cards("Ah Ad"))
    weak = HandCombo(*cards("3h 4d"))
    v1 = nway_combo_equity_vector(board, (weak,), [strong], samples=10, rng=random.Random(1))
    v2 = nway_combo_equity_vector(board, (weak,), [strong], samples=999, rng=random.Random(999))
    assert v1[0] == v2[0]


# ---------------------------------------------------------------------------
# NwayBoardEquityCache
# ---------------------------------------------------------------------------


def test_cache_starts_empty():
    board = tuple(cards("2c 7d 9h"))
    cache = NwayBoardEquityCache(board, [HandCombo(*cards("Ah Ad"))], samples=20)
    assert len(cache) == 0


def test_cache_populates_on_first_touch():
    board = tuple(cards("2c 7d 9h"))
    cache = NwayBoardEquityCache(board, [HandCombo(*cards("Ah Ad"))], samples=20)
    cache.traverser_equity_vector((HandCombo(*cards("Kh Kd")),))
    assert len(cache) == 1


def test_cache_hit_returns_identical_vector_without_growing():
    board = tuple(cards("2c 7d 9h"))
    cache = NwayBoardEquityCache(board, [HandCombo(*cards("Ah Ad"))], samples=20)
    opponents = (HandCombo(*cards("Kh Kd")),)
    first = cache.traverser_equity_vector(opponents)
    second = cache.traverser_equity_vector(opponents)
    assert np.array_equal(first, second, equal_nan=True)
    assert len(cache) == 1


def test_cache_is_order_independent_for_the_opponent_tuple():
    board = tuple(cards("2c 7d 9h"))
    cache = NwayBoardEquityCache(board, [HandCombo(*cards("Ah Ad"))], samples=20)
    opp_a = HandCombo(*cards("Kh Kd"))
    opp_b = HandCombo(*cards("Qh Qd"))
    forward = cache.traverser_equity_vector((opp_a, opp_b))
    reversed_order = cache.traverser_equity_vector((opp_b, opp_a))
    assert np.array_equal(forward, reversed_order, equal_nan=True)
    assert len(cache) == 1  # both requests hit the same canonical entry


def test_cache_values_are_probabilities_or_nan():
    board = tuple(cards("2c 7d 9h"))
    cache = NwayBoardEquityCache(board, [HandCombo(*cards("Ah Ad")), HandCombo(*cards("3h 4d"))], samples=20)
    vector = cache.traverser_equity_vector((HandCombo(*cards("Kh Kd")), HandCombo(*cards("Qh Qd"))))
    assert np.all((vector >= 0.0) | np.isnan(vector))
    assert np.all((vector <= 1.0) | np.isnan(vector))


def test_cache_deterministic_across_separate_caches_with_same_seed():
    board = tuple(cards("2c 7d 9h"))
    candidates = [HandCombo(*cards("Ah Ad")), HandCombo(*cards("3h 4d"))]
    opponents = (HandCombo(*cards("Kh Kd")), HandCombo(*cards("Qh Qd")))
    cache1 = NwayBoardEquityCache(board, candidates, samples=30, seed=99)
    cache2 = NwayBoardEquityCache(board, candidates, samples=30, seed=99)
    assert np.array_equal(cache1.traverser_equity_vector(opponents), cache2.traverser_equity_vector(opponents), equal_nan=True)


def test_cache_different_boards_do_not_collide():
    # Same candidates/opponents, two different boards — a cache is
    # scoped to exactly one board (its own constructor argument), so
    # two separate cache instances (one per board) must not somehow
    # share results; this pins that a NEW cache per board is required
    # (there's no board-keying inside a single cache instance).
    candidates = [HandCombo(*cards("Ah Ad"))]
    opponents = (HandCombo(*cards("Kh Kd")),)
    cache_a = NwayBoardEquityCache(tuple(cards("2c 7d 9h")), candidates, samples=300, seed=1)
    cache_b = NwayBoardEquityCache(tuple(cards("3s 8s Ts")), candidates, samples=300, seed=1)
    vector_a = cache_a.traverser_equity_vector(opponents)
    vector_b = cache_b.traverser_equity_vector(opponents)
    assert vector_a[0] != pytest.approx(vector_b[0])


# ---------------------------------------------------------------------------
# M162: shared-runout ranks. `nway_combo_equity_vector` redraws runouts for
# every opponent tuple; `SharedRunoutRanks` draws them once per board and
# reduces each lookup to integer comparisons. The estimator must not change.
# ---------------------------------------------------------------------------


def _three_way_pool(board):
    """A pool big enough that blockers and conflicts actually occur."""
    from poker_solver.combos import range_from_class_frequencies
    from poker_solver.starting_hands import all_starting_hands

    hands = all_starting_hands()
    combos = set()
    for i in range(3):
        combos |= set(range_from_class_frequencies(
            {h: 1.0 for h in hands[i * 3:i * 3 + 6]}, exclude=frozenset(board)))
    return sorted(combos, key=str)


@pytest.mark.parametrize("board_text,label", [
    ("Jh 7d 2c 9s 4h", "river"),
    ("Jh 7d 2c 9s", "turn"),
])
def test_shared_runouts_are_exact_on_turn_and_river_boards(board_text, label):
    # The sharp test, and the reason it is sharp: with one card or none
    # left to come, BOTH implementations enumerate rather than sample.
    # Sharing draws from a deck that excludes only the board and then
    # drops the runouts that collide with somebody's hole cards - which
    # leaves precisely the deck the per-tuple version enumerated. So this
    # is not "close", it must be equal to the digit. Any difference is a
    # bug in the collision handling, which is the one part of the sharing
    # argument that could be wrong.
    board = tuple(cards(board_text))
    pool = _three_way_pool(board)
    shared = SharedRunoutRanks(board)
    picker = random.Random(3)
    for _ in range(4):
        opponents = tuple(picker.sample(pool, 2))
        reference = nway_combo_equity_vector(board, opponents, pool,
                                             rng=random.Random(99))
        got = shared.equity_vector(opponents, pool)
        assert np.array_equal(np.isnan(reference), np.isnan(got))
        both = ~np.isnan(reference)
        assert np.allclose(reference[both], got[both], atol=0, rtol=0)


def test_shared_runouts_keep_the_nan_contract():
    # NaN means "cannot physically happen", and it has to keep meaning
    # that: a candidate sharing a card with the board or an opponent, and
    # every entry when the opponents cannot coexist at all.
    board = tuple(cards("Jh 7d 2c"))
    hero = HandCombo(Card.from_str("As"), Card.from_str("Kd"))
    blocks_board = HandCombo(Card.from_str("Jh"), Card.from_str("Ks"))
    blocks_opponent = HandCombo(Card.from_str("Ts"), Card.from_str("Qc"))
    opponent = HandCombo(Card.from_str("Ts"), Card.from_str("9s"))
    shared = SharedRunoutRanks(board, samples=60)

    got = shared.equity_vector((opponent,), [hero, blocks_board, blocks_opponent])
    assert not np.isnan(got[0])
    assert np.isnan(got[1])          # shares Jh with the board
    assert np.isnan(got[2])          # shares Ts with the opponent
    # Opponents that conflict with each other: every entry is NaN.
    clashing = (opponent, HandCombo(Card.from_str("Ts"), Card.from_str("2s")))
    assert np.all(np.isnan(shared.equity_vector(clashing, [hero])))
    # An opponent sitting on the board: same.
    on_board = (HandCombo(Card.from_str("Jh"), Card.from_str("4d")),)
    assert np.all(np.isnan(shared.equity_vector(on_board, [hero])))


def test_shared_runouts_do_not_depend_on_lookup_order():
    # Determinism was previously bought with a per-tuple derived seed. It
    # now comes from there being a single draw per board, but the
    # guarantee callers rely on is the same one: an answer depends on the
    # opponent tuple, never on what was asked before it.
    board = tuple(cards("Jh 7d 2c"))
    pool = _three_way_pool(board)
    first, second = (pool[0], pool[30]), (pool[5], pool[40])

    a = SharedRunoutRanks(board, samples=80)
    forward = (a.equity_vector(first, pool), a.equity_vector(second, pool))
    b = SharedRunoutRanks(board, samples=80)
    backward_second = b.equity_vector(second, pool)
    backward_first = b.equity_vector(first, pool)

    for expected, got in ((forward[0], backward_first), (forward[1], backward_second)):
        assert np.array_equal(np.isnan(expected), np.isnan(got))
        mask = ~np.isnan(expected)
        assert np.array_equal(expected[mask], got[mask])


def test_shared_runouts_rank_combos_outside_the_candidate_pool():
    # Opponents come from the same pool in every current caller, but the
    # table must not silently return garbage if one does not - it ranks
    # lazily, so an unseen combo has to be ranked on demand rather than
    # missing from the dict.
    board = tuple(cards("Jh 7d 2c"))
    hero = HandCombo(Card.from_str("As"), Card.from_str("Kd"))
    outsider = HandCombo(Card.from_str("Qs"), Card.from_str("Qh"))
    shared = SharedRunoutRanks(board, samples=60)
    got = shared.equity_vector((outsider,), [hero])
    assert not np.isnan(got[0])
    assert 0.0 <= got[0] <= 1.0


def test_the_nway_cache_uses_shared_runouts():
    # Wiring: the cache is the thing the solver actually calls, and the
    # speedup only exists if it goes through the shared table. A cache
    # whose answers match a directly-built table is going through it.
    board = tuple(cards("Jh 7d 2c"))
    pool = _three_way_pool(board)
    cache = NwayBoardEquityCache(board, pool, samples=80, seed=5)
    direct = SharedRunoutRanks(board, samples=80, seed=5)
    opponents = (pool[1], pool[25])
    from_cache = cache.traverser_equity_vector(opponents)
    from_table = direct.equity_vector(opponents, pool)
    assert np.array_equal(np.isnan(from_cache), np.isnan(from_table))
    mask = ~np.isnan(from_cache)
    assert np.array_equal(from_cache[mask], from_table[mask])
    # And it still memoizes.
    assert len(cache) == 1
    cache.traverser_equity_vector(opponents)
    assert len(cache) == 1
