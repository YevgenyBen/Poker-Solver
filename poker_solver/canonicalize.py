"""Suit-relabeling canonicalization — Phase 2 of the real-time-speed
roadmap (see CLAUDE.md's "### The real-time-speed roadmap" section).

This is an exact, lossless symmetry, deliberately distinct from
abstraction.py's equity-based bucketing (M17/M18): bucketing collapses
*strategically similar but physically different* combos, lossily.
Canonicalization here collapses *physically identical-up-to-suit-
labeling* boards/hands, losslessly — two boards that are literal suit
relabelings of each other (e.g. "2c 7d 9h" and "2h 7s 9c", both
fully-rainbow) represent the exact same strategic spot, and any result
computed for one translates back to the other with zero information
loss.

Phase 2's stated goal (recognizing when two situations are strategically
the same, so a future spot-library lookup can hit instead of every
situation being unique) actually spans three things: board suits, stack
depth, and action-history "shape." Only the first two get new code here.
Action-history shape is already canonical "for free": game_tree.
StreetConfig's fixed raise_sizes/max_raises menu means two situations
solved under the same bet-sizing menu are already directly comparable —
no separate canonicalization step is needed for that piece, only these
other two.

Deliberately NOT here: any cache/lookup table, any CanonicalSituation
bundling type, any wiring into solve_flop/solve_flop_abstracted/
solve_flop_turn/solve_flop_to_river. This module is a standalone
primitive with zero existing callers, same as combos.py (M10, wired in
by M11) and abstraction.py (M17, wired in by M18) — Phase 3 (an offline
precomputed spot library) is what will actually consume this, once its
own key shape is known from building it.
"""

import itertools

from .cards import SUITS, Card
from .combos import HandCombo

DEFAULT_STACK_BUCKET_BB = 5.0


def translate_card(card: Card, suit_map: dict) -> Card:
    """Re-suits `card` via suit_map (real_suit -> some other suit) —
    keeps rank untouched."""
    return Card(card.rank, suit_map[card.suit])


def translate_cards(cards, suit_map: dict) -> tuple:
    """translate_card, applied to every card in an iterable (a board or
    a list of hole cards) — order of the input is preserved."""
    return tuple(translate_card(card, suit_map) for card in cards)


def translate_combo(combo: HandCombo, suit_map: dict) -> HandCombo:
    """Translates both of a combo's cards and reconstructs a HandCombo.

    No separate hole-card rank-ordering logic is needed here:
    HandCombo.__post_init__ already order-normalizes its two cards by
    (value, suit) on construction, so reconstructing from translated
    cards re-runs that normalization automatically — this is a thin
    wrapper, not a reimplementation.
    """
    return HandCombo(translate_card(combo.card_a, suit_map), translate_card(combo.card_b, suit_map))


def invert_suit_map(suit_map: dict) -> dict:
    """canonical_suit -> real_suit — for translating a canonical-space
    result (e.g. a strategy solved against canonicalized combos) back to
    the real suits a caller's actual request used."""
    return {canonical: real for real, canonical in suit_map.items()}


def canonicalize_board(board: tuple) -> tuple:
    """Finds the lexicographically-smallest suit relabeling of `board`
    (rank-then-suit-sorted output) across all 24 possible suit
    permutations — the true minimum over the suit-automorphism group,
    not a single-pass heuristic. Returns (canonical_board, suit_map)
    where suit_map: real_suit -> canonical_suit is total over all 4
    suits (every permutation is a full bijection), so it's ready to
    translate a hero/villain hole card whose suit never appears on the
    board, not just the board's own cards.

    Board-card *order* is deliberately not preserved: canonical_board is
    sorted by (rank, canonical_suit), not dealt order. This is safe
    (best-5-card-hand evaluation never reads board order — confirmed
    across equity.py/board_equity.py) and necessary, not incidental:
    preserving order would let the literal same physical board fail to
    canonicalize together depending on how it happened to be listed —
    directly working against this module's purpose of maximizing a
    future library's hit rate.

    A naive single-pass "walk the board in dealt order, first new suit
    seen gets the next canonical letter" walk was considered and
    rejected — verified, not assumed, to under-collapse boards with a
    repeated rank: across all 22,100 possible flops it produced 1,911
    distinct forms against the true minimum of 1,755, because which of
    two same-rank cards happens to be listed/dealt first is
    strategically meaningless but changes that walk's output (e.g. "2c
    2h 3c" and "2c 2h 3h" are genuinely suit-isomorphic via the c<->h
    swap, but the naive walk canonicalizes them differently). Searching
    the full 24-permutation group avoids this entirely, and turns out
    simpler besides: the winning permutation is already a total
    bijection, so there's no separate "now handle suits absent from the
    board" step to bolt on afterward.
    """
    best_board, best_key, best_map = None, None, None
    for perm in itertools.permutations(SUITS):
        suit_map = dict(zip(SUITS, perm))
        candidate = tuple(
            sorted((translate_card(card, suit_map) for card in board), key=lambda c: (c.value, c.suit))
        )
        # Card has no __lt__ (it's a plain frozen dataclass, not
        # order=True) — compare by each card's own (value, suit) sort
        # key instead of the Card objects themselves.
        candidate_key = tuple((c.value, c.suit) for c in candidate)
        if best_key is None or candidate_key < best_key:
            best_board, best_key, best_map = candidate, candidate_key, suit_map
    return best_board, best_map


def canonical_stack_depth(effective_stack_bb: float, bucket_bb: float = DEFAULT_STACK_BUCKET_BB) -> float:
    """Rounds to the nearest multiple of bucket_bb.

    Uses Python's built-in round(), which is round-half-to-even
    ("banker's rounding"), not round-half-up — an exact-halfway stack
    depth can round either up or down depending on whether the nearest
    multiple's *index* is even or odd (e.g. at the default 5bb bucket:
    12.5 -> 10.0 since 2.5 rounds to 2, but 17.5 -> 20.0 since 3.5
    rounds to 4). Documented here rather than silently relied on, and
    pinned by a test.
    """
    return round(effective_stack_bb / bucket_bb) * bucket_bb
