"""Best 5-card poker hand evaluation.

Given 5-7 cards, finds the best 5-card hand among them and returns a
comparable rank tuple: for two hands, the one with the greater tuple wins
(Python's normal tuple comparison — first differing element decides).

This is used by equity.py to score showdowns during preflop equity
simulation. It has no dependency on the solver/game-tree logic.

Two evaluators live here: `rank_five`/`best_hand_rank` (scalar, one hand
at a time — the original, simplest-possible implementation, and the
permanent trusted reference every other evaluator is validated against)
and `best_hand_rank_batch` (vectorized, many hands at once via NumPy —
added for M9). They're built to compute *the same* comparable ranks by
construction (category priority order, tiebreak order), and
tests/test_hand_eval.py cross-validates them against each other — not
just a random sample, but EXHAUSTIVELY over every one of the 6,188
distinct 5-value hand patterns a real 5-card hand can have (M48) — batch
is a performance path, not an independent algorithm to trust blindly.

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

M48 update: `_rank_five_batch`'s own internals were rewritten from a
per-hand counting/masking/argsort pipeline to a prime-product lookup
table (`_build_value_lookup_table`) — the same technique real production
poker evaluators (the "Cactus Kev"/"Two Plus Two" family) use, adapted
here to stay fully vectorized across NumPy arrays rather than one hand
at a time. Real, measured, not assumed: a genuine end-to-end
`solve_flop_to_river` benchmark (the same one CLAUDE.md's M46/M47
entries used) dropped from ~41s to ~6.5-8s, a ~5-6x real speedup — see
CLAUDE.md's M48 entry for the full profiling/measurement writeup,
including a considered-and-correctly-rejected "lazy chance dispatch"
idea (M47) that this milestone's own finding (hand evaluation, not
chance-tree construction, was the true dominant cost) made moot.
"""

from collections import Counter
from itertools import combinations, combinations_with_replacement

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
# M81: the same thing as an int array, built once at import rather than
# per call to best_hand_rank_batch (which rebuilt it 7,595 times in a
# single 6-max preflop solve).
_COMBO_INDEX = np.array(_FIVE_CARD_COMBOS, dtype=np.int64)
_NUM_FIVE_CARD_COMBOS = len(_FIVE_CARD_COMBOS)


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

    M81: one matrix-vector product instead of a five-step Python loop of
    `tiebreak[:, i].astype(np.int64) * base**(4-i)`. Both inputs are
    already int64 (they are slices of the int64 lookup tables built by
    _build_value_lookup_table), so every one of those `astype` calls was
    copying an array to the type it already had — profiled at **45,570
    astype calls and 6.7s** on a single 6-max preflop solve, with
    _pack_scores itself 12.35s of 102s. Same arithmetic, same result.
    """
    return category * (_VALUE_BASE**5) + tiebreak @ _PACK_WEIGHTS


# Prime per card value (0-12) — a hand's 5 values multiply to a product
# that's unique per *value multiset* (order-independent) by the
# fundamental theorem of arithmetic. The same "prime product" trick
# real production poker evaluators (the "Cactus Kev"/"Two Plus Two"
# family) use for O(1) hand-category lookup instead of per-hand
# counting/sorting — see _build_value_lookup_table and CLAUDE.md's M48
# entry for the real, cross-validated speedup this measured.
# M81: positional weights for _pack_scores, precomputed once.
_PACK_WEIGHTS = np.array([_VALUE_BASE ** (4 - i) for i in range(5)], dtype=np.int64)

_VALUE_PRIMES = np.array([2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41], dtype=np.int64)


def _category_and_tiebreak_for_values(values: tuple) -> tuple:
    """category, 5-tuple tiebreak (zero-padded) for one 5-value multiset,
    IGNORING flush — flush/straight-flush is applied afterward in
    _rank_five_batch, since it depends on suits, not values alone.
    Mirrors rank_five's own counting branch exactly (same Counter/sort-
    by-count-then-value logic), plus straight detection via the same
    trusted _straight_high helper. Used only once, at import time, to
    build the lookup table below — never on any hot path itself."""
    straight_high = _straight_high(set(values))
    if straight_high is not None:
        # A straight needs 5 distinct consecutive values — mutually
        # exclusive with every count-based category below (those all
        # need at least one repeated value), so there's no priority
        # conflict to resolve between the two branches.
        return STRAIGHT, (straight_high, 0, 0, 0, 0)

    value_counts = Counter(values)
    by_count = sorted(value_counts.items(), key=lambda item: (item[1], item[0]), reverse=True)
    counts = [count for _, count in by_count]
    ordered_values = [value for value, _ in by_count]
    padded = tuple((ordered_values + [0, 0, 0, 0, 0])[:5])

    if counts[0] == 4:
        return QUADS, padded
    if counts[0] == 3 and counts[1] == 2:
        return FULL_HOUSE, padded
    if counts[0] == 3:
        return TRIPS, padded
    if counts[0] == 2 and counts[1] == 2:
        return TWO_PAIR, padded
    if counts[0] == 2:
        return PAIR, padded
    return HIGH_CARD, tuple(sorted(values, reverse=True))


def _build_value_lookup_table() -> tuple:
    """Precomputes, once at import time, every distinct 5-value
    multiset's (prime_product, category, tiebreak) — sorted by
    prime_product for np.searchsorted lookups in _rank_five_batch.
    C(13+5-1, 5) = 6,188 distinct multisets (including some no real deck
    can ever deal, e.g. 5-of-a-kind — harmless, they're simply never
    looked up), built once and shared across every call, the same
    "precompute once, reuse forever" idea cards._ALL_CARDS already
    applies (M47)."""
    rows = []
    for values in combinations_with_replacement(range(_VALUE_BASE), 5):
        product = 1
        for v in values:
            product *= int(_VALUE_PRIMES[v])
        category, tiebreak = _category_and_tiebreak_for_values(values)
        rows.append((product, category, *tiebreak))
    rows.sort(key=lambda row: row[0])
    products = np.array([row[0] for row in rows], dtype=np.int64)
    categories = np.array([row[1] for row in rows], dtype=np.int64)
    tiebreaks = np.array([row[2:] for row in rows], dtype=np.int64)
    return products, categories, tiebreaks


_LOOKUP_PRODUCTS, _LOOKUP_CATEGORIES, _LOOKUP_TIEBREAKS = _build_value_lookup_table()


def _rank_five_batch(values: np.ndarray, suits: np.ndarray) -> np.ndarray:
    """values, suits: (M, 5) int arrays (card value 0-12, suit id 0-3).
    Returns (M,) int64 scores — same relative ordering as calling
    rank_five on each row, packed for vectorized comparison.

    Implementation (M48): a prime-product lookup against a precomputed
    table (_build_value_lookup_table), not per-hand counting/sorting —
    see the module docstring and CLAUDE.md's M48 entry for why. A
    flush's value pattern is ALWAYS exactly 5 distinct values (a real
    deck has one card per (value, suit) pair, so 5 same-suit cards can
    never repeat a value), which means the table lookup (built ignoring
    suit entirely) can only ever return STRAIGHT or HIGH_CARD for a
    flush hand — and rank_five's own FLUSH/HIGH_CARD tiebreak
    conventions are identical (both `sorted(values, reverse=True)`), so
    flush is applied afterward as a pure category relabel
    (STRAIGHT->STRAIGHT_FLUSH, HIGH_CARD->FLUSH), never touching the
    already-correct tiebreak.
    """
    is_flush = (suits == suits[:, 0:1]).all(axis=1)
    products = _VALUE_PRIMES[values].prod(axis=1)
    idx = np.searchsorted(_LOOKUP_PRODUCTS, products)
    category = _LOOKUP_CATEGORIES[idx]
    tiebreak = _LOOKUP_TIEBREAKS[idx]

    is_straight = category == STRAIGHT
    is_high_card = category == HIGH_CARD
    category = np.where(is_flush & is_straight, STRAIGHT_FLUSH, category)
    category = np.where(is_flush & is_high_card, FLUSH, category)

    return _pack_scores(category, tiebreak)


def best_hand_rank_batch(values: np.ndarray, suits: np.ndarray) -> np.ndarray:
    """Batched best_hand_rank: values, suits are (M, 7) int arrays (card
    value 0-12, suit id 0-3). Returns (M,) int64 scores — the same
    relative ordering as calling best_hand_rank on each row of 7 cards.
    """
    if values.shape[1] != 7 or suits.shape[1] != 7:
        raise ValueError(f"best_hand_rank_batch requires exactly 7 cards per hand, got {values.shape[1]}")
    m = values.shape[0]
    # M81: _COMBO_INDEX is built once at import. This line used to be
    # `np.array(_FIVE_CARD_COMBOS)`, rebuilding the same (21, 5) array
    # from a Python list of tuples on every single call — 7,595 times in
    # one 6-max preflop solve.
    combo_values = values[:, _COMBO_INDEX].reshape(m * _NUM_FIVE_CARD_COMBOS, 5)
    combo_suits = suits[:, _COMBO_INDEX].reshape(m * _NUM_FIVE_CARD_COMBOS, 5)
    scores = _rank_five_batch(combo_values, combo_suits).reshape(m, _NUM_FIVE_CARD_COMBOS)
    return scores.max(axis=1)
