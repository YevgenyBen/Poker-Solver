import random
from collections import Counter
from itertools import combinations_with_replacement

import numpy as np
import pytest

from poker_solver.cards import SUITS, Card
from poker_solver.hand_utils import RANK_ORDER
from poker_solver.hand_eval import (
    FLUSH,
    FULL_HOUSE,
    HIGH_CARD,
    PAIR,
    QUADS,
    STRAIGHT,
    STRAIGHT_FLUSH,
    TRIPS,
    TWO_PAIR,
    _rank_five_batch,
    best_hand_rank,
    best_hand_rank_batch,
    rank_five,
)


def cards(text: str) -> list:
    """Parse a space-separated string of cards, e.g. 'As Ks Qs Js Ts'."""
    return [Card.from_str(token) for token in text.split()]


def test_rank_five_requires_exactly_five_cards():
    with pytest.raises(ValueError):
        rank_five(cards("As Ks Qs Js"))


def test_royal_flush_category():
    assert rank_five(cards("As Ks Qs Js Ts"))[0] == STRAIGHT_FLUSH


def test_straight_flush_beats_quads():
    straight_flush = rank_five(cards("9s 8s 7s 6s 5s"))
    quads = rank_five(cards("Ah Ad Ac As Kh"))
    assert straight_flush > quads


def test_quads_beats_full_house():
    quads = rank_five(cards("2h 2d 2c 2s Kh"))
    full_house = rank_five(cards("Ah Ad Ac Kh Kd"))
    assert quads > full_house
    assert quads[0] == QUADS
    assert full_house[0] == FULL_HOUSE


def test_full_house_beats_flush():
    full_house = rank_five(cards("2h 2d 2c 3h 3d"))
    flush = rank_five(cards("Ah Kh 9h 5h 2h"))
    assert full_house > flush
    assert flush[0] == FLUSH


def test_flush_beats_straight():
    flush = rank_five(cards("Ah Kh 9h 5h 2h"))
    straight = rank_five(cards("9s 8h 7d 6c 5s"))
    assert flush > straight
    assert straight[0] == STRAIGHT


def test_straight_beats_trips():
    straight = rank_five(cards("9s 8h 7d 6c 5s"))
    trips = rank_five(cards("2h 2d 2c 9h 5h"))
    assert straight > trips
    assert trips[0] == TRIPS


def test_trips_beats_two_pair():
    trips = rank_five(cards("2h 2d 2c 9h 5h"))
    two_pair = rank_five(cards("Ah Ad Kh Kd 2c"))
    assert trips > two_pair
    assert two_pair[0] == TWO_PAIR


def test_two_pair_beats_pair():
    two_pair = rank_five(cards("Ah Ad Kh Kd 2c"))
    pair = rank_five(cards("Ah Ad Kh 9d 2c"))
    assert two_pair > pair
    assert pair[0] == PAIR


def test_pair_beats_high_card():
    pair = rank_five(cards("Ah Ad Kh 9d 2c"))
    high_card = rank_five(cards("Ah Kd 9h 5d 2c"))
    assert pair > high_card
    assert high_card[0] == HIGH_CARD


def test_wheel_straight_is_lowest_straight():
    wheel = rank_five(cards("As 2h 3d 4c 5s"))
    six_high = rank_five(cards("2s 3h 4d 5c 6s"))
    assert wheel[0] == STRAIGHT
    assert wheel < six_high


def test_pair_kicker_breaks_tie():
    better_kicker = rank_five(cards("Ah Ad Kh 9d 2c"))
    worse_kicker = rank_five(cards("Ah Ad Qh 9d 2c"))
    assert better_kicker > worse_kicker


def test_high_card_kicker_breaks_tie():
    better = rank_five(cards("Ah Kd 9h 5d 3c"))
    worse = rank_five(cards("Ah Kd 9h 5d 2c"))
    assert better > worse


def test_best_hand_rank_needs_at_least_five_cards():
    with pytest.raises(ValueError):
        best_hand_rank(cards("As Ks Qs Js"))


def test_best_hand_rank_picks_best_of_seven():
    # Trips among 7 cards, with no flush or straight available — best
    # 5-card hand is the trips plus the two highest kickers.
    seven = cards("2h 2d 2c 9h 5h Kd Qc")
    assert best_hand_rank(seven)[0] == TRIPS


def test_best_hand_rank_uses_best_available_flush_over_pair():
    # A pair among the 7 cards, but 5 of the 7 cards share a suit without
    # being consecutive — best_hand_rank must find the flush, not settle
    # for the pair.
    seven = cards("Ad Ah 2h 4h 7h 9h Kc")
    result = best_hand_rank(seven)
    assert result[0] == FLUSH


# ---------------------------------------------------------------------------
# Vectorized (batch) evaluation — see hand_eval.py's module docstring for
# why this exists (M9: equity computation speed at 6+ players). Batch is
# a performance path over the same logic as rank_five/best_hand_rank
# above, so it's tested primarily by cross-validating it *against* those
# trusted scalar functions across many hands, not by re-deriving expected
# categories from scratch.
# ---------------------------------------------------------------------------


def _card_arrays(card_list: list) -> tuple:
    """(1, N) value/suit int arrays for one hand, matching batch_hand_rank's
    expected input shape (suits as 0-3 ints via SUITS.index)."""
    from poker_solver.cards import SUITS

    values = np.array([[card.value for card in card_list]])
    suits = np.array([[SUITS.index(card.suit) for card in card_list]])
    return values, suits


def _pack_scalar_rank(rank_tuple: tuple) -> int:
    """Packs a scalar rank_five/best_hand_rank tuple the same way
    hand_eval._pack_scores does, for direct comparison against a batch
    score — category*13^5 + tiebreak[0]*13^4 + ... zero-padded to 5
    tiebreak slots."""
    category = rank_tuple[0]
    tiebreak = (list(rank_tuple[1:]) + [0] * 5)[:5]
    score = category * 13**5
    for i, value in enumerate(tiebreak):
        score += value * 13 ** (4 - i)
    return score


def test_rank_five_batch_matches_scalar_for_known_hands():
    known_hands = [
        "As Ks Qs Js Ts",  # royal flush
        "9s 8s 7s 6s 5s",  # straight flush
        "Ah Ad Ac As Kh",  # quads
        "Ah Ad Ac Kh Kd",  # full house
        "Ah Kh 9h 5h 2h",  # flush
        "9s 8h 7d 6c 5s",  # straight
        "As 2h 3d 4c 5s",  # wheel straight
        "2h 2d 2c 9h 5h",  # trips
        "Ah Ad Kh Kd 2c",  # two pair
        "Ah Ad Kh 9d 2c",  # pair
        "Ah Kd 9h 5d 2c",  # high card
    ]
    for text in known_hands:
        card_list = cards(text)
        scalar = _pack_scalar_rank(rank_five(card_list))
        values, suits = _card_arrays(card_list)
        batch = int(_rank_five_batch(values, suits)[0])
        assert batch == scalar, f"mismatch for {text!r}: scalar={scalar} batch={batch}"


def test_best_hand_rank_batch_matches_scalar_for_seven_card_hands():
    seven_card_hands = [
        "2h 2d 2c 9h 5h Kd Qc",  # trips among 7, no flush/straight
        "Ad Ah 2h 4h 7h 9h Kc",  # flush beats a pair among 7
        "As Ks Qs Js Ts 2c 3d",  # royal flush plus junk
    ]
    for text in seven_card_hands:
        card_list = cards(text)
        scalar = _pack_scalar_rank(best_hand_rank(card_list))
        values, suits = _card_arrays(card_list)
        batch = int(best_hand_rank_batch(values, suits)[0])
        assert batch == scalar, f"mismatch for {text!r}: scalar={scalar} batch={batch}"


def test_best_hand_rank_batch_requires_exactly_seven_cards():
    values = np.zeros((1, 5), dtype=int)
    suits = np.zeros((1, 5), dtype=int)
    with pytest.raises(ValueError):
        best_hand_rank_batch(values, suits)


def test_best_hand_rank_batch_cross_validates_against_scalar_randomly():
    # The real correctness signal: many random hands, not just fixed
    # known cases — verified during M9 development at 30,000 trials with
    # zero mismatches; kept smaller here to stay a fast test.
    rng = random.Random(20240613)
    all_cards = [(v, s) for v in range(13) for s in range(4)]
    n_trials = 300

    for _ in range(n_trials):
        dealt = rng.sample(all_cards, 7)
        card_list = [Card(rank="23456789TJQKA"[v], suit="cdhs"[s]) for v, s in dealt]
        scalar = _pack_scalar_rank(best_hand_rank(card_list))
        values, suits = _card_arrays(card_list)
        batch = int(best_hand_rank_batch(values, suits)[0])
        assert batch == scalar, f"mismatch for {dealt!r}: scalar={scalar} batch={batch}"


# ---------------------------------------------------------------------------
# M48: _rank_five_batch's internals were rewritten to a prime-product
# lookup-table design (see hand_eval.py's own module comments) for real
# speed, not just correctness-preserving refactoring — so this gets the
# strongest correctness signal available: EXHAUSTIVE, not sampled,
# coverage of every one of the C(13+5-1, 5) = 6,188 distinct 5-value
# multisets a real 5-card hand can have, each cross-validated against
# the trusted scalar rank_five reference. Mirrors this project's own
# "exhaustive enumeration where feasible" precedent (M19's flop/turn
# canonicalization tests) rather than trusting a random sample alone.
# ---------------------------------------------------------------------------


def _cards_for_value_multiset(values: tuple, all_same_suit: bool) -> list:
    """Real, physically-valid Card objects for one 5-value multiset — a
    repeated value gets a DIFFERENT suit each time it recurs (a real
    deck has only one card per exact rank+suit pair), cycling through
    SUITS per distinct value. `all_same_suit=True` additionally forces
    every card to suit 's' (only physically valid when every value in
    the multiset is distinct — a real deck can't deal two same-suit
    same-value cards)."""
    if all_same_suit:
        return [Card(RANK_ORDER[v], "s") for v in values]
    next_suit_index: dict = {}
    result = []
    for v in values:
        i = next_suit_index.get(v, 0)
        result.append(Card(RANK_ORDER[v], SUITS[i]))
        next_suit_index[v] = i + 1
    return result


def test_rank_five_batch_exhaustive_over_every_value_multiset():
    for values in combinations_with_replacement(range(13), 5):
        if max(Counter(values).values()) > 4:
            continue  # not physically dealable from a real (4-suit) deck

        card_list = _cards_for_value_multiset(values, all_same_suit=False)
        scalar = _pack_scalar_rank(rank_five(card_list))
        value_arr, suit_arr = _card_arrays(card_list)
        batch = int(_rank_five_batch(value_arr, suit_arr)[0])
        assert batch == scalar, f"mismatch (non-flush) for {values!r}: scalar={scalar} batch={batch}"

        if len(set(values)) == 5:
            # The only case a real deck could ever deal as a flush —
            # every value distinct, so all 5 cards can share one suit.
            flush_cards = _cards_for_value_multiset(values, all_same_suit=True)
            scalar_flush = _pack_scalar_rank(rank_five(flush_cards))
            value_arr2, suit_arr2 = _card_arrays(flush_cards)
            batch_flush = int(_rank_five_batch(value_arr2, suit_arr2)[0])
            assert batch_flush == scalar_flush, f"mismatch (flush) for {values!r}: scalar={scalar_flush} batch={batch_flush}"


def test_rank_five_batch_processes_many_hands_at_once():
    hands = ["As Ks Qs Js Ts", "2h 2d 2c 9h 5h", "Ah Kd 9h 5d 2c"]
    card_lists = [cards(text) for text in hands]
    values = np.array([[c.value for c in cl] for cl in card_lists])
    from poker_solver.cards import SUITS

    suits = np.array([[SUITS.index(c.suit) for c in cl] for cl in card_lists])
    scores = _rank_five_batch(values, suits)
    assert scores.shape == (3,)
    # Royal flush > trips > high card, same ordering as the scalar checks above.
    assert scores[0] > scores[1] > scores[2]
