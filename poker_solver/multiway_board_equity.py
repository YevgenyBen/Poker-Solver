"""N-way, board-aware combo-level equity.

Phase 1 of the diagnostic's own scoped-out §4/#5 item
(docs/full-table-diagnostic-2026-08.md): true multiway (3+ live)
postflop solving needs the *intersection* of two properties neither
existing equity primitive has both of at once —
equity.py's MultiwayEquityCache is N-way but always deals a fresh
random 5-card board from nothing (preflop-only, board-blind);
board_equity.py's build_board_equity_table is board-aware but strictly
pairwise (two combos at a time, never more). This module is that
intersection: given a real, fixed board, a fixed tuple of opponent
combos, and a pool of candidate combos, compute each candidate's real
N-way win-share on that specific board.

Ships as a standalone primitive with zero existing callers, matching
this project's own established M10-then-M11 precedent
(combos.py/board_equity.py shipped a full milestone before solve_flop
wired them into a real solve) — wiring this into a real MCCFR solve is
deliberately a separate, future milestone. The diagnostic's own §4
named three OTHER prerequisites beyond this one, none attempted here:
a signature-level change threading a per-chance-branch equity source
through MCCFR's terminal-value computation; per-position range seeding
and opponent sampling (MCCFR currently has no way to seed a position's
real derived range, and samples every opponent from one global
preflop-style prior regardless of position); and a chance-branch
sampling case in the MCCFR recursion itself.

A load-bearing lesson carried forward from M27, not rediscovered here:
this module deliberately does NOT substitute any placeholder value
(e.g. a flat 1/n_live) for a candidate that can't physically be dealt
alongside the fixed opponents/board — every such entry is NaN instead
(the same convention board_equity.py already established for its own
impossible matchups). M27 measured, in the preflop/class-level case,
that injecting *any* constant placeholder for an "impossible today,
but reached anyway by an opponent sampler with no card-removal
tracking" combination compounds destructively under CFR+'s regret
flooring — a better constant helped, but never fully fixed it; what
actually worked was rejecting and resampling *before* needing a
placeholder at all (see cfr.py's MAX_OPPONENT_RESAMPLE_ATTEMPTS). A
future milestone wiring this module into MCCFR needs to apply that
same discipline (reject-and-resample opponent/candidate combos before
calling this function with something already known to be impossible),
not invent a fresh placeholder-value question this module's own NaN
convention already refuses to answer on its own.
"""

import hashlib
import random

import numpy as np

from .cards import remaining_deck
from .combos import HandCombo
from .hand_eval import best_hand_rank_batch

DEFAULT_NWAY_BOARD_EQUITY_SAMPLES = 200
DEFAULT_SEED = 42

_SUIT_INDEX = {suit: i for i, suit in enumerate("cdhs")}


def _stable_seed(*parts) -> int:
    """A seed derived from `parts` that's stable across process restarts
    — mirrors equity.py's own private helper of the same name/shape
    (kept as its own small copy here rather than a cross-module import,
    the same "just duplicate the shared-shape utility" precedent
    cards.remaining_deck's own promotion already set for equity.py's
    pre-existing _remaining_deck: a new shared shape for new code, not
    a retrofit of what already works).
    """
    joined = "|".join(str(part) for part in parts)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def nway_combo_equity_vector(
    board: tuple,
    opponent_combos: tuple,
    candidate_combos: list,
    samples: int = DEFAULT_NWAY_BOARD_EQUITY_SAMPLES,
    rng: random.Random | None = None,
) -> np.ndarray:
    """Length-len(candidate_combos) array: index i is the win-share (a
    k-way tie splits 1/k) of a player holding candidate_combos[i],
    against the *fixed* `opponent_combos`, on this specific `board`.

    NaN wherever candidate_combos[i] can't physically coexist with the
    board or any opponent combo (shares a card), or wherever the
    opponent combos can't coexist with EACH OTHER or the board (every
    entry is NaN in that case — no candidate could ever be evaluated
    against an impossible opponent tuple) — see this module's own
    docstring for why no placeholder is substituted here.

    Each candidate deals its own Monte Carlo runouts, correctly
    excluding that candidate's own two cards from the deck (not shared
    across candidates) — the precise, not-approximated version of the
    same "which cards are actually still live" question chance.py's own
    M12 entry names as a real, precisely-bounded approximation it
    accepted elsewhere (uniform chance-branch weight regardless of a
    hand's own blockers); this module doesn't need to accept that
    shortcut, since it deals each candidate's runouts fresh.

    `remaining_needed <= 1` (a river board, or a turn board with only
    the river left) is resolved *exactly* — every possible single-card
    runout enumerated, not sampled — mirroring board_equity.py's own
    identical optimization for the identical reason (cheaper, noise-
    free, and there's nothing left to sample at 0 remaining cards).
    """
    rng = rng if rng is not None else random.Random(DEFAULT_SEED)
    result = np.full(len(candidate_combos), np.nan, dtype=float)

    board = tuple(board)
    board_set = frozenset(board)
    remaining_needed = 5 - len(board)
    if remaining_needed < 0:
        raise ValueError(f"board has {len(board)} cards, at most 5 are allowed")

    opponent_cards = [card for combo in opponent_combos for card in combo.cards]
    if len(set(opponent_cards)) != len(opponent_cards) or any(card in board_set for card in opponent_cards):
        return result  # opponents mutually conflict, or conflict with the board — every entry stays NaN

    board_values = np.array([card.value for card in board], dtype=np.int64)
    board_suits = np.array([_SUIT_INDEX[card.suit] for card in board], dtype=np.int64)
    num_opponents = len(opponent_combos)

    # M80: ONE set of runouts, shared by every candidate, with each
    # candidate ignoring the samples that collide with its own two cards.
    #
    # The loop this replaces gave every candidate its own deck (excluding
    # that candidate's cards) and therefore its own runouts — precise, and
    # the docstring above defends it as such. The cost was that the k
    # opponent hands were re-ranked once per candidate: work of
    # `candidates x samples x (1 + k)` where `samples x (candidates + k)`
    # suffices. At 120 candidates against 2 opponents that is ~3x more
    # hand evaluations than necessary, and after M79 removed the
    # interpreter overhead, hand evaluation was 75% of the request.
    #
    # Sharing costs precision in one specific, bounded way: runouts are
    # drawn from a deck that excludes only the board and the opponents, so
    # ~8% of them collide with a given candidate's own cards. Those
    # samples are dropped FOR THAT CANDIDATE rather than redrawn, which
    # would destroy the sharing. That is a variance cost, not a bias one —
    # which board cards a candidate blocks depends only on its own cards,
    # never on how well it does — and it is the same trade M68 made in
    # equity._simulate_equity_shared_board for the same reason.
    shared_used = board_set | frozenset(opponent_cards)
    deck = remaining_deck(shared_used)
    if remaining_needed > len(deck):
        return result  # cannot complete the board at all — every entry stays NaN

    deck_values = np.fromiter((card.value for card in deck), dtype=np.int64, count=len(deck))
    deck_suits = np.fromiter(
        (_SUIT_INDEX[card.suit] for card in deck), dtype=np.int64, count=len(deck)
    )
    deck_ids = deck_values * 4 + deck_suits

    if remaining_needed == 0:
        runout_values = np.empty((1, 0), dtype=np.int64)
        runout_suits = np.empty((1, 0), dtype=np.int64)
        runout_ids = np.empty((1, 0), dtype=np.int64)
    elif remaining_needed == 1:
        # Exact enumeration, unchanged: every single-card runout.
        runout_values = deck_values[:, None]
        runout_suits = deck_suits[:, None]
        runout_ids = deck_ids[:, None]
    else:
        generator = np.random.default_rng(rng.getrandbits(64))
        keys = generator.random((samples, len(deck)))
        picked = np.argpartition(keys, remaining_needed - 1, axis=1)[:, :remaining_needed]
        runout_values = deck_values[picked]
        runout_suits = deck_suits[picked]
        runout_ids = deck_ids[picked]

    num_samples = runout_values.shape[0]
    full_values = np.concatenate(
        [np.broadcast_to(board_values, (num_samples, len(board_values))), runout_values], axis=1
    )
    full_suits = np.concatenate(
        [np.broadcast_to(board_suits, (num_samples, len(board_suits))), runout_suits], axis=1
    )

    def _score(combos_to_score):
        """Rank each of `combos_to_score` on every shared runout."""
        count = len(combos_to_score)
        values = np.empty((count, num_samples, 7), dtype=np.int64)
        suits = np.empty((count, num_samples, 7), dtype=np.int64)
        for position, entry in enumerate(combos_to_score):
            values[position, :, 0] = entry.card_a.value
            values[position, :, 1] = entry.card_b.value
            suits[position, :, 0] = _SUIT_INDEX[entry.card_a.suit]
            suits[position, :, 1] = _SUIT_INDEX[entry.card_b.suit]
        values[:, :, 2:] = full_values[None, :, :]
        suits[:, :, 2:] = full_suits[None, :, :]
        return best_hand_rank_batch(
            values.reshape(count * num_samples, 7), suits.reshape(count * num_samples, 7)
        ).reshape(count, num_samples)

    # Opponents: ranked ONCE for all candidates, instead of once each.
    if num_opponents:
        opponent_scores = _score(opponent_combos)
        best_opponent = opponent_scores.max(axis=0)
        opponents_at_best = (opponent_scores == best_opponent[None, :]).sum(axis=0)
    else:
        best_opponent = np.full(num_samples, np.iinfo(np.int64).min, dtype=np.int64)
        opponents_at_best = np.zeros(num_samples, dtype=np.int64)

    playable = [
        (idx, entry)
        for idx, entry in enumerate(candidate_combos)
        if not (entry.blocks(board_set) or entry.blocks(opponent_cards))
    ]
    if not playable:
        return result

    candidate_scores = _score([entry for _, entry in playable])
    candidate_ids = np.array(
        [
            [entry.card_a.value * 4 + _SUIT_INDEX[entry.card_a.suit],
             entry.card_b.value * 4 + _SUIT_INDEX[entry.card_b.suit]]
            for _, entry in playable
        ],
        dtype=np.int64,
    )
    collides = (
        runout_ids[None, :, :, None] == candidate_ids[:, None, None, :]
    ).any(axis=3).any(axis=2)
    usable = ~collides

    wins = candidate_scores > best_opponent[None, :]
    ties = candidate_scores == best_opponent[None, :]
    shares = np.where(wins, 1.0, 0.0) + np.where(
        ties, 1.0 / (1.0 + opponents_at_best[None, :]), 0.0
    )
    usable_counts = usable.sum(axis=1)
    totals = np.where(usable, shares, 0.0).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        equities = np.where(usable_counts > 0, totals / usable_counts, np.nan)
    for position, (idx, _entry) in enumerate(playable):
        result[idx] = equities[position]

    return result


# ---------------------------------------------------------------------------
# Shared-runout ranks (M162).
#
# `nway_combo_equity_vector` above draws its own runouts for every
# opponent tuple, so the same candidate is re-ranked on fresh boards
# thousands of times in one solve. Measured on a real multiway flop:
# 99% of the solve is these lookups, and 96% of a lookup is
# `best_hand_rank_batch`.
#
# Nothing about a candidate's rank on a given runout depends on WHICH
# opponents it is being compared against - only the comparison does. So
# the ranks can be computed once per board and every lookup reduced to
# integer comparisons.
#
# The one thing that has to change to allow it: runouts drawn once
# cannot exclude each opponent tuple's hole cards. They are drawn
# excluding the board alone, and samples colliding with a candidate OR
# an opponent are DROPPED for that lookup. That is not an approximation
# - rejection sampling from the larger deck gives exactly the same
# conditional distribution as drawing from the smaller one - it costs
# effective sample count, which is why `SHARED_RUNOUT_SAMPLES` is set
# above the per-tuple default rather than equal to it. This is the same
# trade `nway_combo_equity_vector` already makes for the candidate's own
# two cards, extended to the opponents'.
# ---------------------------------------------------------------------------

# Raised above DEFAULT_NWAY_BOARD_EQUITY_SAMPLES (200) because ~23% of
# shared runouts are dropped per lookup at three-handed (six hole cards
# against a 49-card deck) where the per-tuple version drops ~8%. 320
# keeps the EFFECTIVE count at or above what the per-tuple default
# delivered; samples are close to free here, since they are paid once
# per board instead of once per opponent tuple.
SHARED_RUNOUT_SAMPLES = 320


def _card_bit_index(card) -> int:
    """0-51 index for a card, used to build set-membership masks."""
    return card.value * 4 + _SUIT_INDEX[card.suit]


class SharedRunoutRanks:
    """Every combo's 7-card rank on ONE shared set of runouts for a board.

    Built lazily per combo: a combo is ranked the first time it is asked
    for, then reused for every opponent tuple it ever appears in.

    Deterministic given `seed`: the runouts depend only on (seed, board),
    so a lookup's answer depends on the opponent tuple alone and not on
    the order tuples happen to be requested in - the same guarantee
    `NwayBoardEquityCache` already made, by a simpler route (there is now
    one draw, not one per tuple).
    """

    def __init__(self, board: tuple, samples: int = SHARED_RUNOUT_SAMPLES,
                 seed: int = DEFAULT_SEED):
        self.board = tuple(board)
        board_set = frozenset(self.board)
        remaining_needed = 5 - len(self.board)
        if remaining_needed < 0:
            raise ValueError(
                f"board has {len(self.board)} cards, at most 5 are allowed"
            )

        deck = remaining_deck(board_set)
        deck_values = np.fromiter((c.value for c in deck), dtype=np.int64,
                                  count=len(deck))
        deck_suits = np.fromiter((_SUIT_INDEX[c.suit] for c in deck),
                                 dtype=np.int64, count=len(deck))

        if remaining_needed == 0:
            picked = np.empty((1, 0), dtype=np.int64)
        elif remaining_needed == 1:
            # Enumerated, not sampled — and still exact once collisions
            # are dropped, because the dropped cards are precisely the
            # ones the players' own hole cards make impossible.
            picked = np.arange(len(deck), dtype=np.int64)[:, None]
        elif remaining_needed > len(deck):
            picked = np.empty((0, 0), dtype=np.int64)
        else:
            generator = np.random.default_rng(_stable_seed(seed, str(self.board)))
            keys = generator.random((samples, len(deck)))
            picked = np.argpartition(keys, remaining_needed - 1,
                                     axis=1)[:, :remaining_needed]

        self._runout_values = deck_values[picked]
        self._runout_suits = deck_suits[picked]
        self.num_samples = self._runout_values.shape[0]

        board_values = np.array([c.value for c in self.board], dtype=np.int64)
        board_suits = np.array([_SUIT_INDEX[c.suit] for c in self.board],
                               dtype=np.int64)
        self._full_values = np.concatenate([
            np.broadcast_to(board_values, (self.num_samples, len(board_values))),
            self._runout_values], axis=1)
        self._full_suits = np.concatenate([
            np.broadcast_to(board_suits, (self.num_samples, len(board_suits))),
            self._runout_suits], axis=1)

        # Which of the 52 cards each runout uses, for collision masks.
        runout_ids = self._runout_values * 4 + self._runout_suits
        self._runout_bits = np.zeros((self.num_samples, 52), dtype=bool)
        for column in range(runout_ids.shape[1]):
            self._runout_bits[np.arange(self.num_samples), runout_ids[:, column]] = True

        self._board_bits = np.zeros(52, dtype=bool)
        for card in self.board:
            self._board_bits[_card_bit_index(card)] = True

        self._rank_of: dict = {}
        self._blocked_of: dict = {}
        self._bits_of: dict = {}

    def _ensure(self, combos: list) -> None:
        """Rank any combo not seen before, in one batch."""
        missing = [c for c in combos if c not in self._rank_of]
        if not missing or self.num_samples == 0:
            for combo in missing:
                self._rank_of[combo] = np.empty(0, dtype=np.int64)
                self._blocked_of[combo] = np.empty(0, dtype=bool)
                self._bits_of[combo] = self._combo_bits(combo)
            return

        count = len(missing)
        values = np.empty((count, self.num_samples, 7), dtype=np.int64)
        suits = np.empty((count, self.num_samples, 7), dtype=np.int64)
        for position, combo in enumerate(missing):
            values[position, :, 0] = combo.card_a.value
            values[position, :, 1] = combo.card_b.value
            suits[position, :, 0] = _SUIT_INDEX[combo.card_a.suit]
            suits[position, :, 1] = _SUIT_INDEX[combo.card_b.suit]
        values[:, :, 2:] = self._full_values[None, :, :]
        suits[:, :, 2:] = self._full_suits[None, :, :]
        ranks = best_hand_rank_batch(
            values.reshape(count * self.num_samples, 7),
            suits.reshape(count * self.num_samples, 7),
        ).reshape(count, self.num_samples)

        for position, combo in enumerate(missing):
            bits = self._combo_bits(combo)
            self._bits_of[combo] = bits
            self._rank_of[combo] = ranks[position]
            self._blocked_of[combo] = self._runout_bits[:, bits].any(axis=1)

    def _combo_bits(self, combo) -> np.ndarray:
        return np.array([_card_bit_index(combo.card_a),
                         _card_bit_index(combo.card_b)], dtype=np.int64)

    def equity_vector(self, opponent_combos: tuple, candidate_combos: list
                      ) -> np.ndarray:
        """Win-share of each candidate against this fixed opponent tuple.

        Same estimator and same NaN contract as
        `nway_combo_equity_vector` — NaN wherever a candidate cannot
        physically coexist with the board or an opponent, and NaN
        everywhere if the opponents conflict with each other or the
        board.
        """
        result = np.full(len(candidate_combos), np.nan, dtype=float)
        opponents = list(opponent_combos)

        # Opponents must be able to coexist with the board and each other.
        seen = set()
        for combo in opponents:
            for card in combo.cards:
                if card in seen or self._board_bits[_card_bit_index(card)]:
                    return result
                seen.add(card)

        if self.num_samples == 0:
            return result

        self._ensure(opponents + list(candidate_combos))

        if opponents:
            opponent_ranks = np.stack([self._rank_of[c] for c in opponents])
            best_opponent = opponent_ranks.max(axis=0)
            opponents_at_best = (opponent_ranks == best_opponent[None, :]).sum(axis=0)
            opponent_blocked = np.zeros(self.num_samples, dtype=bool)
            for combo in opponents:
                opponent_blocked |= self._blocked_of[combo]
        else:
            best_opponent = np.full(self.num_samples, np.iinfo(np.int64).min,
                                    dtype=np.int64)
            opponents_at_best = np.zeros(self.num_samples, dtype=np.int64)
            opponent_blocked = np.zeros(self.num_samples, dtype=bool)

        opponent_cards = frozenset(seen)
        for index, combo in enumerate(candidate_combos):
            if combo.blocks(frozenset(self.board)) or combo.blocks(opponent_cards):
                continue
            usable = ~(self._blocked_of[combo] | opponent_blocked)
            usable_count = int(usable.sum())
            if usable_count == 0:
                continue
            ranks = self._rank_of[combo]
            shares = np.where(ranks > best_opponent, 1.0, 0.0)
            ties = ranks == best_opponent
            shares += np.where(ties, 1.0 / (1.0 + opponents_at_best), 0.0)
            result[index] = float(shares[usable].sum()) / usable_count

        return result


class NwayBoardEquityCache:
    """Lazy, memoized N-way board-aware equity: computes and caches a
    candidate pool's full equity vector against one *fixed* tuple of
    opponent combos, on one *fixed* board, only the first time that
    exact opponent-combo combination is actually needed against this
    cache's own board — the same lazy-memoization architecture
    equity.py's MultiwayEquityCache already established for the
    preflop, board-blind case (M8), adapted here for board-awareness
    and combo-level (not class-level) granularity.

    Deterministic given `seed`: each cache entry's Monte Carlo run uses
    a seed derived from (`seed`, this cache's own board, the opponent
    combos) via `_stable_seed`, not a single shared advancing RNG — so
    results don't depend on which order different opponent-combo
    combinations happen to be requested in, only on the combination
    itself (mirrors MultiwayEquityCache's own determinism guarantee and
    reasoning exactly).
    """

    def __init__(
        self,
        board: tuple,
        candidate_combos: list,
        samples: int = SHARED_RUNOUT_SAMPLES,
        seed: int = DEFAULT_SEED,
    ):
        """M162: `samples` now counts runouts drawn ONCE for this board and
        shared by every opponent tuple, not runouts redrawn per tuple —
        which is why its default rose from 200 to 320. See
        `SharedRunoutRanks` for why sharing is sound and what it costs.
        """
        self.board = tuple(board)
        self.candidate_combos = candidate_combos
        self.samples = samples
        self.seed = seed
        self._cache: dict = {}
        self._ranks = SharedRunoutRanks(self.board, samples=samples, seed=seed)

    def traverser_equity_vector(self, opponent_combos: tuple) -> np.ndarray:
        key = tuple(sorted(opponent_combos, key=str))
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        vector = self._ranks.equity_vector(opponent_combos, self.candidate_combos)
        self._cache[key] = vector
        return vector

    def __len__(self) -> int:
        """Number of distinct opponent-combo combinations cached so far."""
        return len(self._cache)
