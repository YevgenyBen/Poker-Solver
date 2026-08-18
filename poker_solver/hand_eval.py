"""Best 5-card poker hand evaluation.

Given 5-7 cards, finds the best 5-card hand among them and returns a
comparable rank tuple: for two hands, the one with the greater tuple wins
(Python's normal tuple comparison — first differing element decides).

This is used by equity.py to score showdowns during preflop equity
simulation. It has no dependency on the solver/game-tree logic.

Two evaluators live here: `rank_five`/`best_hand_rank` (scalar, one hand
at a time — the original, simplest-possible implementation) and
`best_hand_rank_batch` (vectorized, many hands at once via NumPy — added
for M9). They're built to compute *the same* comparable ranks by
construction (category priority order, tiebreak order), and
tests/test_hand_eval.py cross-validates them against each other across
many random hands, not just fixed known cases — batch is a performance
path, not an independent algorithm to trust blindly.

Why batch exists: equity.py's Monte Carlo simulation calls this once per
(candidate hand, opponent combination, sampled board) — at N=2/3 players
that's cheap, but at N=6-9 the sheer number of distinct opponent-hand
combinations MCCFR touches (see equity.py's MultiwayEquityCache
docstring) makes the scalar path's per-call Python overhead (Counter,
sorting, 21 five-card combos per 7-card hand, all pure Python) the
dominant cost of solving at all — measured during M9 at 0.15-0.7s per
equity-vector computation, making real MCCFR iteration counts (100K+)
infeasible. Batch evaluates many (hand, board) combinations in one
vectorized pass instead of one Python call each.
"""

from collections import Counter
from itertools import combinations

import numpy as np

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

# Card values run 0-12 (see hand_utils.rank_value) — used as the base for
# packing a whole rank tuple into one comparable integer (see
# _pack_scores) since NumPy has no notion of comparing tuples elementwise.
_VALUE_BASE = 13
_FIVE_CARD_COMBOS = list(combinations(range(7), 5))  # the 21 five-of-seven choices


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


# ---------------------------------------------------------------------------
# Vectorized (batch) evaluation — see the module docstring for why this
# exists. Computes the same category-then-tiebreakers ranking as
# rank_five/best_hand_rank above, just for many hands at once, and packs
# the result into a single comparable int64 per hand instead of a tuple
# (NumPy has no elementwise tuple comparison) — see _pack_scores.
# ---------------------------------------------------------------------------


def _pack_scores(category: np.ndarray, tiebreak: np.ndarray) -> np.ndarray:
    """category: (M,) int array. tiebreak: (M, 5) int array, values 0-12,
    zero-padded past however many tiebreakers a category actually uses
    (e.g. a straight only fills tiebreak[:, 0], a flush fills all 5).
    Packing zero-pads *safely*: within one category every hand has the
    same number of "real" tiebreaker slots (a category is defined by its
    value-count shape), so the padding never competes with a real value,
    and across categories the category digit dominates regardless.
    """
    score = category.astype(np.int64) * (_VALUE_BASE**5)
    for i in range(5):
        score = score + tiebreak[:, i].astype(np.int64) * (_VALUE_BASE ** (4 - i))
    return score


def _rank_five_batch(values: np.ndarray, suits: np.ndarray) -> np.ndarray:
    """values, suits: (M, 5) int arrays (card value 0-12, suit id 0-3).
    Returns (M,) int64 scores — same relative ordering as calling
    rank_five on each row, packed for vectorized comparison.
    """
    m = values.shape[0]
    is_flush = (suits == suits[:, 0:1]).all(axis=1)

    value_range = np.arange(_VALUE_BASE)
    # counts[:, v] = how many of this hand's 5 cards have value v
    counts = (values[:, :, None] == value_range[None, None, :]).sum(axis=1)  # (M, 13)
    presence = counts > 0
    distinct_count = presence.sum(axis=1)
    max_count = counts.max(axis=1)

    # Straight detection: the wheel (A-2-3-4-5, i.e. values {0,1,2,3,12})
    # plus the 9 "normal" 5-consecutive-value runs (high value 4..12).
    # A straight requires 5 *distinct* values spanning exactly one such
    # run, so at most one pattern can ever match a given hand.
    straight_high = np.full(m, -1, dtype=np.int64)
    wheel = presence[:, 0] & presence[:, 1] & presence[:, 2] & presence[:, 3] & presence[:, 12]
    straight_high[wheel] = 3  # "5-high" straight, matching rank_five's low+4 for the wheel
    for high in range(4, _VALUE_BASE):
        low = high - 4
        matches = presence[:, low] & presence[:, low + 1] & presence[:, low + 2] & presence[:, low + 3] & presence[:, high]
        straight_high[matches] = high
    is_straight = straight_high >= 0

    # Values ordered by (count desc, value desc) — mirrors rank_five's
    # `sorted(value_counts.items(), key=lambda item: (item[1], item[0]), reverse=True)`.
    # Absent values (count 0) get key -1 so they always sort last.
    key = np.where(presence, counts * 100 + value_range[None, :], -1)
    order = np.argsort(-key, axis=1)[:, :5]
    ordered_values = np.broadcast_to(value_range, (m, _VALUE_BASE))[np.arange(m)[:, None], order]

    category = np.zeros(m, dtype=np.int64)
    tiebreak = np.zeros((m, 5), dtype=np.int64)

    def _apply(mask, cat, num_tiebreaks, source=ordered_values):
        category[mask] = cat
        for i in range(num_tiebreaks):
            tiebreak[mask, i] = source[mask, i] if source is ordered_values else source[mask]

    # Checked in standard poker priority order, highest first. Categories
    # are mutually exclusive by construction (same-suit cards can't share
    # a value, so a flush always has max_count==1 / distinct_count==5;
    # a straight likewise needs exactly 5 distinct values) — see the
    # module docstring's cross-validation note for how this is verified.
    is_straight_flush = is_flush & is_straight
    _apply(is_straight_flush, STRAIGHT_FLUSH, 1, source=straight_high)
    remaining = ~is_straight_flush

    is_quads = remaining & (max_count == 4)
    _apply(is_quads, QUADS, 2)

    is_full_house = remaining & (max_count == 3) & (distinct_count == 2)
    _apply(is_full_house, FULL_HOUSE, 2)

    is_flush_only = remaining & is_flush
    _apply(is_flush_only, FLUSH, 5)

    is_straight_only = remaining & is_straight & (~is_flush)
    _apply(is_straight_only, STRAIGHT, 1, source=straight_high)

    is_trips = remaining & (max_count == 3) & (distinct_count == 3)
    _apply(is_trips, TRIPS, 3)

    is_two_pair = remaining & (max_count == 2) & (distinct_count == 3)
    _apply(is_two_pair, TWO_PAIR, 3)

    is_pair = remaining & (max_count == 2) & (distinct_count == 4)
    _apply(is_pair, PAIR, 4)

    is_high_card = remaining & (max_count == 1) & (~is_straight) & (~is_flush)
    _apply(is_high_card, HIGH_CARD, 5)

    return _pack_scores(category, tiebreak)


def best_hand_rank_batch(values: np.ndarray, suits: np.ndarray) -> np.ndarray:
    """Batched best_hand_rank: values, suits are (M, 7) int arrays (card
    value 0-12, suit id 0-3). Returns (M,) int64 scores — the same
    relative ordering as calling best_hand_rank on each row of 7 cards.
    """
    if values.shape[1] != 7 or suits.shape[1] != 7:
        raise ValueError(f"best_hand_rank_batch requires exactly 7 cards per hand, got {values.shape[1]}")
    m = values.shape[0]
    combo_idx = np.array(_FIVE_CARD_COMBOS)  # (21, 5)
    combo_values = values[:, combo_idx].reshape(m * len(_FIVE_CARD_COMBOS), 5)
    combo_suits = suits[:, combo_idx].reshape(m * len(_FIVE_CARD_COMBOS), 5)
    scores = _rank_five_batch(combo_values, combo_suits).reshape(m, len(_FIVE_CARD_COMBOS))
    return scores.max(axis=1)
