"""Board-aware combo-level equity — the postflop counterpart to
equity.py's class-level preflop equity.

Once a board is known, comparing two concrete hand combos is a different
problem than preflop equity solves: blocker effects (does my hand block
your outs) are a first-order concern here, not an approximation to
ignore — so this module reasons about combos.HandCombo (concrete
two-card hands), not starting_hands.StartingHand (169 abstract classes).
See combos.py's module docstring for the full reasoning behind that
choice.

Kept in its own module rather than folded into equity.py to avoid a
circular import: combos.py already imports equity.py's `_suit_pairs_for`
helper, and this module needs combos.py's HandCombo.
"""

import random

import numpy as np

from .cards import SUITS, Card, remaining_deck
from .combos import HandCombo
from .hand_eval import best_hand_rank_batch

DEFAULT_BOARD_EQUITY_SAMPLES = 200
DEFAULT_SEED = 42

# Measured during M10 (this implementation, samples=200, a 3-card flop
# board): a 23-combo range (a handful of classes) built in ~1.1s; a
# 78-combo range (a dozen-plus classes, spanning several pairs and
# broadways) took ~19s. That's roughly O(N^2) in combo count, as
# expected for a pairwise table, and confirms the per-pair loop below
# (batched across *samples* within one matchup, but not across
# different matchups) is fine for the small-to-moderate curated ranges
# M10/M11 actually use — the same "prove the mechanics with a small
# known input first" scope M8/M9 used — but would take tens of minutes
# at the full ~1176-combo scale a wide real range could reach. Fully
# batching across matchups (not just within one) is the natural next
# optimization if/when a milestone actually needs that scale; not done
# here since nothing yet needs it (measured, not assumed, per the
# project's standing discipline for this kind of tradeoff).

_SUIT_INDEX = {suit: i for i, suit in enumerate(SUITS)}

# M129: the runout arrays for an already-complete board — no cards to
# draw, so a single empty "runout" that broadcasts to nothing.
_EMPTY_RUNOUT = np.empty((1, 0), dtype=np.int64)


def build_board_equity_table(
    board: tuple,
    combos: list,
    samples: int = DEFAULT_BOARD_EQUITY_SAMPLES,
    rng: random.Random | None = None,
) -> np.ndarray:
    """N x N table: table[i, j] = combos[i]'s average equity vs
    combos[j], given the fixed `board`, averaged over Monte Carlo
    runouts of whatever community cards remain (2 for a flop board, 1
    for a turn board, 0 — exact, not sampled — for a complete river
    board). Deterministic for a given `rng` seed.

    table[i, j] is NaN wherever i == j or combos[i]/combos[j] share a
    card with each other or with the board — physically impossible
    matchups (a real opponent can never hold your card, and you can't
    hold a card already on the board). Never read by a valid reach
    vector, but the array still needs a defined fill value rather than
    garbage memory.

    `remaining_needed <= 1` (a river board, or a turn board with only
    the river left to come) is resolved *exactly* — every possible
    single-card runout enumerated, not sampled — rather than Monte Carlo
    averaged like `remaining_needed >= 2` still is; `samples` and `rng`
    are silently unused in that case (there's nothing left to sample).
    This is both cheaper and noise-free at the turn level, which matters
    for chance.py's chained flop->turn solving (M12): every branch table
    it builds is exactly this remaining_needed==1 case.
    """
    rng = rng if rng is not None else random.Random(DEFAULT_SEED)
    n = len(combos)
    table = np.full((n, n), np.nan, dtype=float)

    board_cards = list(board)
    remaining_needed = 5 - len(board_cards)
    if remaining_needed < 0:
        raise ValueError(f"board has {len(board_cards)} cards, at most 5 are allowed")

    board_values = [card.value for card in board_cards]
    board_suits = [_SUIT_INDEX[card.suit] for card in board_cards]
    board_set = frozenset(board_cards)
    # M129: array forms, hoisted out of the O(N^2) pair loop.
    n_board = len(board_cards)
    board_values_arr = np.asarray(board_values, dtype=np.int64)
    board_suits_arr = np.asarray(board_suits, dtype=np.int64)
    # One NumPy generator for the whole table, seeded FROM `rng` so the
    # function stays deterministic in exactly the same way it was: same
    # `rng` seed in, same table out.
    np_rng = np.random.default_rng(rng.getrandbits(63))

    # Combos blocked by the board itself can never win a share of this
    # table — every row/column for them stays NaN, exactly like i == j.
    valid = [(i, combo) for i, combo in enumerate(combos) if not combo.blocks(board_set)]

    for a_pos, (i, combo_i) in enumerate(valid):
        for j, combo_j in valid[a_pos + 1 :]:
            if combo_i.blocks(combo_j.cards):
                continue  # share a card with each other — impossible matchup, leave NaN

            used = board_set | frozenset(combo_i.cards) | frozenset(combo_j.cards)
            deck = remaining_deck(used)

            # M129: runouts are drawn and laid out with NumPy rather than
            # `rng.sample` in a Python loop. Profiled on a cold flop
            # request, the old form was 1.5M `random.sample` calls and
            # ~16% of total wall time — pure overhead inside the O(N^2)
            # pair loop, with no bearing on what gets solved.
            #
            # Interleaved A/B in one process (M70's method, because this
            # machine drifts): 1.32x on the table itself. Statistically
            # equivalent rather than bit-identical — the RNG stream
            # changes, so at 4,000 samples the mean cell moves 0.0055 and
            # the worst 0.027, which is Monte Carlo noise between two
            # independent streams, not a behaviour change. Still fully
            # deterministic for a given seed.
            if remaining_needed == 0:
                m = 1
                runout_values = _EMPTY_RUNOUT
                runout_suits = _EMPTY_RUNOUT
            elif remaining_needed == 1:
                # exactly one card left — enumerate, don't sample
                m = len(deck)
                runout_values = np.fromiter((card.value for card in deck),
                                            dtype=np.int64, count=m).reshape(m, 1)
                runout_suits = np.fromiter((_SUIT_INDEX[card.suit] for card in deck),
                                           dtype=np.int64, count=m).reshape(m, 1)
            else:
                m = samples
                deck_values = np.fromiter((card.value for card in deck),
                                          dtype=np.int64, count=len(deck))
                deck_suits = np.fromiter((_SUIT_INDEX[card.suit] for card in deck),
                                         dtype=np.int64, count=len(deck))
                # `samples` draws of `remaining_needed` DISTINCT cards in
                # one call: a random key per card, partially sorted, take
                # the lowest few. argpartition is O(len(deck)) per row
                # where a full sort would be O(n log n), and taking the
                # k smallest keys is equivalent to a uniform draw without
                # replacement.
                keys = np_rng.random((samples, len(deck)))
                idx = np.argpartition(keys, remaining_needed - 1, axis=1)[:, :remaining_needed]
                runout_values = deck_values[idx]
                runout_suits = deck_suits[idx]

            values = np.empty((m, 2, 7), dtype=np.int64)
            suits = np.empty((m, 2, 7), dtype=np.int64)
            for hand_idx, combo in enumerate((combo_i, combo_j)):
                values[:, hand_idx, 0] = combo.card_a.value
                values[:, hand_idx, 1] = combo.card_b.value
                suits[:, hand_idx, 0] = _SUIT_INDEX[combo.card_a.suit]
                suits[:, hand_idx, 1] = _SUIT_INDEX[combo.card_b.suit]
            # the board is the same for both hands and every runout; the
            # runout is the same for both hands within a sample
            values[:, :, 2:2 + n_board] = board_values_arr
            suits[:, :, 2:2 + n_board] = board_suits_arr
            values[:, :, 2 + n_board:] = runout_values[:, None, :]
            suits[:, :, 2 + n_board:] = runout_suits[:, None, :]

            scores = best_hand_rank_batch(values.reshape(m * 2, 7), suits.reshape(m * 2, 7)).reshape(m, 2)
            wins_i = int((scores[:, 0] > scores[:, 1]).sum())
            ties = int((scores[:, 0] == scores[:, 1]).sum())
            equity_i = (wins_i + 0.5 * ties) / m

            table[i, j] = equity_i
            table[j, i] = 1.0 - equity_i

    return table


def two_combo_equity(
    board: tuple,
    combo_a: HandCombo,
    combo_b: HandCombo,
    samples: int = DEFAULT_BOARD_EQUITY_SAMPLES,
    rng: random.Random | None = None,
) -> tuple:
    """(equity_a, equity_b) for exactly two combos on a given board — a
    convenience wrapper around build_board_equity_table for the common
    "just compare two hands" case (e.g. an equity calculator), so a
    caller doesn't need to build/index an N x N table for N=2. Raises
    ValueError (via build_board_equity_table) if the two combos or
    either combo and the board share a card.
    """
    table = build_board_equity_table(board, [combo_a, combo_b], samples=samples, rng=rng)
    if np.isnan(table[0, 1]):
        raise ValueError(f"{combo_a} and {combo_b} can't both be dealt with board {board!r}")
    return float(table[0, 1]), float(table[1, 0])
