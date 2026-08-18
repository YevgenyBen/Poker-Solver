import pytest

from poker_solver.cards import Card
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
    best_hand_rank,
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
