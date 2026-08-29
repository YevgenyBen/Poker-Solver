"""Tests for poker_solver/hand_strength.py (M166)."""

import numpy as np
import pytest

from poker_solver.cards import Card
from poker_solver.combos import HandCombo
from poker_solver.hand_strength import strength_percentile


def board(text: str) -> tuple:
    return tuple(Card.from_str(token) for token in text.split())


def hand(text: str) -> HandCombo:
    a, b = text.split()
    return HandCombo(Card.from_str(a), Card.from_str(b))


def test_strength_orders_hands_the_way_a_player_would():
    """The property the reliability signal depends on. If this ordering is
    wrong, the API attaches the wrong reliability note to every answer."""
    flop = board("As Ks 5h")
    ranked = [
        ("Ah Ad", "set of aces"),
        ("Ac 9d", "top pair"),
        ("Kc 4d", "second pair"),
        ("7h 2d", "air"),
    ]
    scores = [strength_percentile(hand(cards), flop) for cards, _ in ranked]
    assert scores == sorted(scores, reverse=True), dict(zip(
        [label for _, label in ranked], scores))
    assert scores[0] > 0.95      # a set is near the top
    assert scores[-1] < 0.20     # air is near the bottom


@pytest.mark.parametrize("cards,expected_length", [
    ("As Ks 5h", 3),
    ("As Ks 5h 2c", 4),
    ("As Ks 5h 2c 9d", 5),
])
def test_strength_works_on_every_postflop_street(cards, expected_length):
    """Flop, turn and river give 5, 6 and 7 cards. Only 5 and 7 have a
    batch evaluator, so six is scored as the best of its six five-card
    subsets — the case most likely to be silently wrong."""
    table = board(cards)
    assert len(table) == expected_length
    strong = strength_percentile(hand("Ah Ad"), table)
    weak = strength_percentile(hand("7h 3d"), table)
    assert 0.0 <= weak < strong <= 1.0
    assert strong > 0.9 and weak < 0.2


def test_the_turn_card_actually_counts():
    """The six-card branch takes the best of six five-card subsets, and a
    weaker implementation — keeping the last subset instead of the maximum
    — silently ignores the last board card.

    A first version of this test compared a pair against air and passed
    under exactly that mutation, because a pair beats air with or without
    the turn. This uses a hand the TURN makes: deuces are a modest pair on
    As Ks 5h and a set once the 2c arrives. If the turn card is being
    dropped, the score collapses.
    """
    flop = board("As Ks 5h")
    turn = board("As Ks 5h 2c")
    deuces = hand("2h 2d")

    on_flop = strength_percentile(deuces, flop)
    on_turn = strength_percentile(deuces, turn)
    # The lowest pair still beats ~61% of hands, because most random hands
    # miss the board entirely — an assumption worth pinning, since a first
    # version of this test guessed it sat below 0.55 and was wrong.
    assert 0.55 < on_flop < 0.70, on_flop     # a modest pair
    assert on_turn > 0.90, on_turn            # a set, near the top
    assert on_turn - on_flop > 0.30

    # And the same hand keeps its set on the river.
    river = board("As Ks 5h 2c 9d")
    assert strength_percentile(deuces, river) > 0.90


def test_strength_refuses_a_hand_that_cannot_be_held():
    """A hero sharing a card with the board is impossible. Returning a
    number for it would put a confident reliability note on a hand nobody
    can hold."""
    with pytest.raises(ValueError, match="shares a card"):
        strength_percentile(hand("As Kd"), board("As Ks 5h"))


def test_strength_refuses_a_board_that_is_not_a_street():
    with pytest.raises(ValueError, match="3, 4 or 5"):
        strength_percentile(hand("As Kd"), board("2c 3d"))


def test_strength_is_a_fraction_and_is_deterministic():
    flop = board("Jh 7d 2c")
    hero = hand("Qs Qd")
    first = strength_percentile(hero, flop)
    assert 0.0 <= first <= 1.0
    assert first == strength_percentile(hero, flop)


def test_strength_is_measured_against_every_hand_not_a_range():
    """Deliberate design choice, pinned: a range-relative measure would
    inherit whatever is wrong with the modelled range, which is the exact
    thing this signal exists to warn about. The check is that a hand's
    score does not depend on anything but the hand and the board — there
    is no range argument to pass.
    """
    import inspect

    signature = inspect.signature(strength_percentile)
    assert list(signature.parameters) == ["hero", "board"]
