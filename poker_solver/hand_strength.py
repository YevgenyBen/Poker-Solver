"""How strong is one hand on one board, relative to every other hand?

M166. Not a solver input — a reporting primitive. The postflop advice's
accuracy depends sharply on this quantity, so the API uses it to say
which of its own answers are reliable rather than labelling all of them
the same way.

Measured over 44 flop spots drawn from real play across three studies:

    band          n   mean error   worst   over 0.10
    0.00-0.20    10       0.0510   0.182       3
    0.20-0.40    10       0.2686   0.993       4
    0.40-0.55     7       0.0745   0.270       2
    0.55-0.75     8       0.1252   0.990       1
    0.75-0.90     7       0.0079   0.023       0
    0.90-1.01     2       0.0374   0.057       0

**Strength does NOT predict error** — the correlation is -0.130, and
errors appear at every band below 0.65. An earlier version of this
module claimed a weak/strong split on 27 spots; the next 18 broke it
(M166 asserted it, M167 withdrew it).

What survives is one-sided: the top of the range is clean. Nothing at or
above 0.75 exceeded 0.10 error. So the API certifies reliability there
and reports it as unknown below, rather than claiming a split that is
not in the data. See api/config.py's RELIABLE_HAND_STRENGTH_PERCENTILE.

Deliberately ranks against EVERY two-card combination that can coexist
with the board, not against a modelled range. A range-relative measure
would inherit whatever is wrong with the range, which is exactly the
thing this is trying to warn about.
"""

from __future__ import annotations

import numpy as np

from .cards import remaining_deck
from .combos import HandCombo
from .hand_eval import _rank_five_batch, best_hand_rank_batch

_SUIT_INDEX = {"c": 0, "d": 1, "h": 2, "s": 3}


def _best_rank(values: np.ndarray, suits: np.ndarray) -> np.ndarray:
    """Best five-card rank for hands of 5, 6 or 7 cards.

    The two batch evaluators cover exactly 5 and exactly 7 — a turn board
    gives six, so those are scored as the best of their six five-card
    subsets. Doing it by subsets rather than reaching for a new evaluator
    keeps this on primitives the suite already exercises.
    """
    count = values.shape[1]
    if count == 5:
        return _rank_five_batch(values, suits)
    if count == 7:
        return best_hand_rank_batch(values, suits)
    if count != 6:
        raise ValueError(f"expected 5, 6 or 7 cards per hand, got {count}")
    best = None
    for drop in range(6):
        keep = [i for i in range(6) if i != drop]
        ranks = _rank_five_batch(values[:, keep], suits[:, keep])
        best = ranks if best is None else np.maximum(best, ranks)
    return best


def strength_percentile(hero: HandCombo, board: tuple) -> float:
    """Fraction of possible hands this one beats on this board, 0.0-1.0.

    1.0 means nothing else does better; 0.0 means everything does. Ties
    count as not-beaten, so an average hand sits slightly below its
    nominal position — consistent across hands, which is all the
    threshold needs.

    `board` may be a flop, turn or river. Raises if hero shares a card
    with the board, since that hand cannot be held.
    """
    board = tuple(board)
    if not 3 <= len(board) <= 5:
        raise ValueError(f"board must have 3, 4 or 5 cards, got {len(board)}")
    board_set = frozenset(board)
    if hero.blocks(board_set):
        raise ValueError(f"{hero} shares a card with the board {board}")

    deck = list(remaining_deck(board_set))
    combos = [HandCombo(a, b) for i, a in enumerate(deck) for b in deck[i + 1:]]
    if hero not in combos:          # hero is board-legal, so this is defensive
        combos.append(hero)

    values = np.array(
        [[c.value for c in (x.card_a, x.card_b, *board)] for x in combos],
        dtype=np.int64,
    )
    suits = np.array(
        [[_SUIT_INDEX[c.suit] for c in (x.card_a, x.card_b, *board)] for x in combos],
        dtype=np.int64,
    )
    ranks = _best_rank(values, suits)
    return float((ranks < ranks[combos.index(hero)]).mean())
