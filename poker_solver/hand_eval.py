"""Best 5-card poker hand evaluation.

Given 5-7 cards, finds the best 5-card hand among them and returns a
comparable rank tuple: for two hands, the one with the greater tuple wins
(Python's normal tuple comparison — first differing element decides).

This is used by equity.py to score showdowns during preflop equity
simulation. It has no dependency on the solver/game-tree logic.
"""

from collections import Counter
from itertools import combinations

from .cards import Card

# Hand categories, low to high — the first element of every rank tuple.
HIGH_CARD = 0
PAIR = 1
TWO_PAIR = 2
TRIPS = 3
STRAIGHT = 4
FLUSH = 5
FULL_HOUSE = 6
QUADS = 7
STRAIGHT_FLUSH = 8


def _straight_high(values: set) -> int | None:
    """Highest card value of a straight within `values`, or None.

    An ace (value 12) also counts as low, for the wheel (A-2-3-4-5) — this
    is checked by additionally allowing an ace to stand in for value -1.
    """
    check = set(values)
    if 12 in check:
        check.add(-1)
    best = None
    for low in sorted(check):
        if all(low + offset in check for offset in range(5)):
            best = low + 4
    return best


def rank_five(cards: list) -> tuple:
    """Rank exactly 5 cards. Higher returned tuple = stronger hand."""
    if len(cards) != 5:
        raise ValueError(f"rank_five requires exactly 5 cards, got {len(cards)}")

    values = [card.value for card in cards]
    suits = [card.suit for card in cards]
    is_flush = len(set(suits)) == 1
    straight_high = _straight_high(set(values))

    if is_flush and straight_high is not None:
        return (STRAIGHT_FLUSH, straight_high)

    value_counts = Counter(values)
    by_count = sorted(value_counts.items(), key=lambda item: (item[1], item[0]), reverse=True)
    counts = [count for _, count in by_count]
    ordered_values = [value for value, _ in by_count]

    if counts[0] == 4:
        return (QUADS, *ordered_values)
    if counts[0] == 3 and counts[1] == 2:
        return (FULL_HOUSE, *ordered_values)
    if is_flush:
        return (FLUSH, *sorted(values, reverse=True))
    if straight_high is not None:
        return (STRAIGHT, straight_high)
    if counts[0] == 3:
        return (TRIPS, *ordered_values)
    if counts[0] == 2 and counts[1] == 2:
        return (TWO_PAIR, *ordered_values)
    if counts[0] == 2:
        return (PAIR, *ordered_values)
    return (HIGH_CARD, *sorted(values, reverse=True))


def best_hand_rank(cards: list) -> tuple:
    """Best 5-card rank achievable by choosing 5 of the given 5-7 cards."""
    if len(cards) < 5:
        raise ValueError(f"Need at least 5 cards to rank a hand, got {len(cards)}")
    if len(cards) == 5:
        return rank_five(cards)
    return max(rank_five(list(combo)) for combo in combinations(cards, 5))
