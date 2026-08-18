"""Preflop all-in equity between starting-hand classes.

Equity between two StartingHand classes is estimated via Monte Carlo
simulation: deal concrete (mutually distinct) hole cards for each class,
sample random 5-card boards from the rest of the deck, and score each
showdown with hand_eval.best_hand_rank.

This intentionally does not weight by exact combo-vs-combo blocker
effects between the two players' hands — each class is treated as a
single unit with one representative combo per matchup. That's a known,
documented approximation for a preflop-only solver (see the project plan).

The full 169x169 table is expensive to build from scratch (tens of
thousands of matchups); build_equity_table/get_equity_table are meant to
be run once and cached to disk, not recomputed on every solve.
"""

import random
from pathlib import Path

import numpy as np

from .cards import SUITS, Card
from .hand_eval import best_hand_rank
from .hand_utils import RANK_ORDER
from .starting_hands import StartingHand, all_starting_hands

DEFAULT_SAMPLES = 200
DEFAULT_SEED = 42
DEFAULT_CACHE_PATH = Path(__file__).parent / "data" / "preflop_equity.npy"

_ALL_HANDS = all_starting_hands()
_HAND_INDEX = {hand: i for i, hand in enumerate(_ALL_HANDS)}


def hand_index(hand: StartingHand) -> int:
    """This hand class's row/column index in the canonical equity table."""
    return _HAND_INDEX[hand]


def _suit_pairs_for(hand: StartingHand) -> list:
    """Candidate (suit_for_high, suit_for_low) pairs respecting `hand`.

    Pairs and offsuit hands need two different suits; suited hands need
    the same suit twice.
    """
    if hand.suited and not hand.is_pair:
        return [(suit, suit) for suit in SUITS]
    return [(s1, s2) for s1 in SUITS for s2 in SUITS if s1 != s2]


def deal_two_hands(hand_a: StartingHand, hand_b: StartingHand):
    """Pick 4 concrete, mutually-distinct cards for hand_a and hand_b.

    Two different starting-hand classes can share a rank (e.g. AKs vs
    AQo both contain an ace), so their representative cards must be
    chosen together to guarantee no physical card is used twice.
    """
    for suit_a in _suit_pairs_for(hand_a):
        card_a1 = Card(hand_a.high_rank, suit_a[0])
        card_a2 = Card(hand_a.low_rank, suit_a[1])
        for suit_b in _suit_pairs_for(hand_b):
            card_b1 = Card(hand_b.high_rank, suit_b[0])
            card_b2 = Card(hand_b.low_rank, suit_b[1])
            dealt = {card_a1, card_a2, card_b1, card_b2}
            if len(dealt) == 4:
                return (card_a1, card_a2), (card_b1, card_b2)
    raise RuntimeError(f"Could not deal distinct cards for {hand_a} vs {hand_b}")


def _remaining_deck(used: list) -> list:
    used_set = set(used)
    return [
        Card(rank, suit)
        for rank in RANK_ORDER
        for suit in SUITS
        if Card(rank, suit) not in used_set
    ]


def monte_carlo_equity(
    hand_a: StartingHand,
    hand_b: StartingHand,
    samples: int = DEFAULT_SAMPLES,
    rng: random.Random | None = None,
) -> float:
    """hand_a's all-in equity against hand_b, via random board runouts.

    Ties split the pot (0.5 each). Deterministic for a given `rng` seed.
    """
    rng = rng if rng is not None else random.Random(DEFAULT_SEED)
    (card_a1, card_a2), (card_b1, card_b2) = deal_two_hands(hand_a, hand_b)
    deck = _remaining_deck([card_a1, card_a2, card_b1, card_b2])

    wins = 0.0
    for _ in range(samples):
        board = rng.sample(deck, 5)
        rank_a = best_hand_rank([card_a1, card_a2, *board])
        rank_b = best_hand_rank([card_b1, card_b2, *board])
        if rank_a > rank_b:
            wins += 1.0
        elif rank_a == rank_b:
            wins += 0.5
    return wins / samples


def build_equity_table(
    hands: list | None = None,
    samples: int = DEFAULT_SAMPLES,
    seed: int = DEFAULT_SEED,
) -> np.ndarray:
    """Build an NxN equity table (hands[i]'s equity against hands[j]).

    Only the upper triangle is actually simulated — the lower triangle is
    filled in as 1 - equity by construction, so the result is exactly
    symmetric (equity(i, j) == 1 - equity(j, i)) and the diagonal is
    exactly 0.5.
    """
    hands = hands if hands is not None else _ALL_HANDS
    n = len(hands)
    table = np.full((n, n), 0.5, dtype=float)
    rng = random.Random(seed)
    for i in range(n):
        for j in range(i + 1, n):
            equity_ij = monte_carlo_equity(hands[i], hands[j], samples=samples, rng=rng)
            table[i, j] = equity_ij
            table[j, i] = 1.0 - equity_ij
    return table


def get_equity_table(
    cache_path: Path | str | None = None,
    hands: list | None = None,
    samples: int = DEFAULT_SAMPLES,
    seed: int = DEFAULT_SEED,
    force_rebuild: bool = False,
) -> np.ndarray:
    """Load the equity table from disk, building and caching it if needed.

    Building the full 169x169 table from scratch takes real time (tens of
    thousands of Monte Carlo matchups) — this is meant to be paid once,
    with subsequent calls served from the cached .npy file.
    """
    path = Path(cache_path) if cache_path is not None else DEFAULT_CACHE_PATH
    if not force_rebuild and path.exists():
        return np.load(path)
    table = build_equity_table(hands=hands, samples=samples, seed=seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(path, table)
    return table
