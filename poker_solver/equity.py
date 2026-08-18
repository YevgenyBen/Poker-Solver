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

import hashlib
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


# ---------------------------------------------------------------------------
# N-way (multiway) equity.
#
# A full N-way equity table is never viable to precompute eagerly — the
# unique-matchup count explodes with N (169-choose-3-with-repeats alone
# is ~819k, versus ~14k for pairs), so multiway equity is instead
# computed lazily and cached per opponent-hand-tuple the first time a
# solve actually needs it — see MultiwayEquityCache below. This is also
# the mechanism a much larger N (6/9-max) will need regardless, since a
# full table is never an option there at all.
# ---------------------------------------------------------------------------

MULTIWAY_DEFAULT_SAMPLES = 200  # measured during M8: at 50 samples, equity
# noise for a matchup that should be an exact coinflip (e.g. a pocket
# pair against the identical pocket pair held by an opponent) got
# amplified by the all-in pot size into a large enough value error to
# visibly distort MCCFR's learned strategy — not just "a bit less
# precise". 200 (matching the pairwise DEFAULT_SAMPLES) fixed it in
# testing without meaningfully hurting solve speed, since this is a
# lazy per-matchup cost paid once per unique opponent combination
# actually encountered, not an eager one-time build.


def deal_n_hands(hands: list) -> list:
    """Pick concrete, mutually-distinct cards for N StartingHand classes.

    Generalizes deal_two_hands via backtracking (assign one hand's cards,
    filter the next hand's candidates against what's already used,
    recurse, backtrack on dead ends) — this stays correct, not just
    "usually works", as N grows toward 6/9-max later.
    """
    assignment = [None] * len(hands)

    def backtrack(index: int, used: set) -> bool:
        if index == len(hands):
            return True
        for suit_a, suit_b in _suit_pairs_for(hands[index]):
            card_a = Card(hands[index].high_rank, suit_a)
            card_b = Card(hands[index].low_rank, suit_b)
            if card_a == card_b or card_a in used or card_b in used:
                continue
            assignment[index] = (card_a, card_b)
            used.add(card_a)
            used.add(card_b)
            if backtrack(index + 1, used):
                return True
            used.discard(card_a)
            used.discard(card_b)
            assignment[index] = None
        return False

    if not backtrack(0, set()):
        raise RuntimeError(f"Could not deal distinct cards for {hands}")
    return assignment


def monte_carlo_equity_n(
    hands: list,
    samples: int = MULTIWAY_DEFAULT_SAMPLES,
    rng: random.Random | None = None,
) -> list:
    """Each hand's all-in win-share (a k-way tie splits 1/k) against all
    the others simultaneously, via random shared-board runouts.

    Returned list is the same length and order as `hands`. Deterministic
    for a given `rng` seed.
    """
    rng = rng if rng is not None else random.Random(DEFAULT_SEED)
    dealt = deal_n_hands(hands)
    used_cards = [card for pair in dealt for card in pair]
    deck = _remaining_deck(used_cards)

    wins = [0.0] * len(hands)
    for _ in range(samples):
        board = rng.sample(deck, 5)
        ranks = [best_hand_rank([card_a, card_b, *board]) for card_a, card_b in dealt]
        best_rank = max(ranks)
        winners = [i for i, rank in enumerate(ranks) if rank == best_rank]
        share = 1.0 / len(winners)
        for i in winners:
            wins[i] += share
    return [total / samples for total in wins]


def _stable_seed(*parts) -> int:
    """A seed derived from `parts` that's stable across process restarts
    — unlike Python's built-in hash() for strings/StartingHand, which is
    randomized per-process by default, this must reproduce the same
    value today, tomorrow, and in CI, for "same seed -> same result" to
    actually hold across separate runs, not just within one process.
    """
    joined = "|".join(str(part) for part in parts)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


class MultiwayEquityCache:
    """Lazy, memoized N-way equity: computes and caches a traverser's
    full equity vector (one value per candidate hand in `hands`, default
    all 169 classes) against one *fixed* tuple of opponent hands, only
    the first time that exact opponent combination is actually needed.

    Deterministic given `seed`: each cache entry's Monte Carlo run uses
    a seed derived from (`seed`, the opponent hands) via `_stable_seed`,
    not a single shared advancing RNG — so results don't depend on which
    order different opponent-hand combinations happen to be requested
    in, only on the combination itself.
    """

    def __init__(
        self,
        hands: list | None = None,
        samples: int = MULTIWAY_DEFAULT_SAMPLES,
        seed: int = DEFAULT_SEED,
    ):
        self.hands = hands if hands is not None else _ALL_HANDS
        self.samples = samples
        self.seed = seed
        self._cache: dict = {}

    def traverser_equity_vector(self, opponent_hands: tuple) -> np.ndarray:
        """Length-len(self.hands) array: index i is the win-share of a
        traverser holding self.hands[i], against the fixed
        `opponent_hands` (in any order — the result doesn't depend on
        which opponent holds which hand, only the traverser's own)."""
        key = tuple(sorted(opponent_hands, key=str))
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        rng = random.Random(_stable_seed(self.seed, *key))
        values = []
        for hand in self.hands:
            try:
                equity = monte_carlo_equity_n([hand, *opponent_hands], samples=self.samples, rng=rng)[0]
            except RuntimeError:
                # `hand` can't physically be dealt alongside these exact
                # opponent hands — e.g. two opponents both holding KK
                # already accounts for all 4 kings, blocking a third KK.
                # This is a genuine card-removal effect that the
                # project's "ignore blockers" approximation (reach
                # probability doesn't depend on opponents' specific
                # hands) doesn't fully cover once opponent hands are
                # *fixed* rather than a distribution — but the true
                # probability of this exact combination is 0 regardless
                # of what equity we'd assign it, so any neutral
                # placeholder is fine; it just needs to not crash.
                equity = 0.5
            values.append(equity)

        vector = np.array(values)
        self._cache[key] = vector
        return vector

    def __len__(self) -> int:
        """Number of distinct opponent-hand combinations cached so far."""
        return len(self._cache)
