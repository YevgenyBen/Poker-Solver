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


SHARED_RUNOUT_FLOP_SAMPLES = 320
"""Runouts drawn per BOARD for `build_shared_runout_equity_table` (M176).

Higher than the per-pair builder's default because shared runouts cannot
exclude each pair's hole cards and colliding draws are DROPPED instead —
rejection sampling, which gives exactly the conditional distribution the
per-pair form enumerates but costs effective sample count. Same reasoning
and same number as `multiway_board_equity.SHARED_RUNOUT_SAMPLES` (M162).
**Do not lower this to the per-pair default thinking it matches.**
"""


def build_shared_runout_equity_table(
    board: tuple,
    combos: list,
    samples: int = SHARED_RUNOUT_FLOP_SAMPLES,
    rng: random.Random | None = None,
) -> np.ndarray:
    """The same table as `build_board_equity_table`, built by ranking each
    combo ONCE per runout instead of once per pair.

    M176, and the heads-up sibling of M162's `SharedRunoutRanks`. The
    per-pair builder draws fresh runouts for every (i, j) and ranks both
    hands on them, but **a combo's rank on a runout does not depend on who
    it is compared against — only the comparison does**. So the work is
    O(N x samples) rankings plus O(N^2) integer comparisons, where the
    per-pair form is O(N^2 x samples) rankings.

    Why this mattered enough to build: the equity table measured **89.5%**
    of a cold flop request (8.62s cold, 7.71s table, 0.41s CFR) — the
    inverse of M155's 14/86, because M161 made CFR O(N) and M172 tripled
    the combo count the O(N^2) table scales with.

    Measured, interleaved in one process (M70), against the per-pair
    builder at its shipped 30 samples:

        cap  26 ( 164 combos)   1.28s ->  0.23s    5.6x
        cap  60 ( 417 combos)   8.49s ->  0.78s   10.9x
        cap 100 ( 708 combos)  25.44s ->  1.68s   15.1x

    The win grows with pool size, as O(N^2) -> O(N) predicts.

    **It is also more accurate, not merely faster**, because 320 shared
    samples net more usable runouts than 30 per-pair ones. Against a
    4,000-sample truth on Th5s7c: per-pair mean 0.0496 / worst 0.3373;
    shared mean 0.0166 / worst 0.1067. The per-pair builder disagrees with
    ITSELF under a different seed by more (0.0679) than shared disagrees
    with truth.

    **Collisions are DROPPED, not excluded.** A runout sharing a card with
    either combo cannot be used for that pair, so it is masked out — which
    is rejection sampling from the larger deck and therefore exactly the
    conditional distribution of the smaller one. On TURN and RIVER boards
    both builders enumerate, so dropping collisions leaves precisely the
    deck the per-pair form walks and the two agree **to the digit** (4,422
    cells, 0.0 difference) — that equivalence is the correctness evidence,
    and `tests/test_board_equity.py` pins it.

    A pair whose every shared runout collides is left NaN, exactly as a
    pair sharing a card is: the caller's `nan_to_num` contract is
    unchanged.
    """
    rng = rng if rng is not None else random.Random(DEFAULT_SEED)
    board_cards = tuple(board)
    board_set = frozenset(board_cards)
    remaining_needed = 5 - len(board_cards)
    if remaining_needed < 0:
        raise ValueError("board cannot hold more than five cards")

    size = len(combos)
    table = np.full((size, size), np.nan, dtype=np.float64)
    valid = [(i, combo) for i, combo in enumerate(combos) if not combo.blocks(board_set)]
    if not valid:
        return table

    deck = remaining_deck(board_set)
    np_rng = np.random.default_rng(rng.getrandbits(63))
    if remaining_needed == 0:
        runouts = [()]
    elif remaining_needed == 1:
        # Exactly one card left — enumerate, never sample. `samples` is
        # ignored here for the same reason it is in the per-pair builder
        # (M154), which is what makes turn/river boards exactly comparable.
        runouts = [(card,) for card in deck]
    else:
        keys = np_rng.random((samples, len(deck)))
        idx = np.argpartition(keys, remaining_needed - 1, axis=1)[:, :remaining_needed]
        runouts = [tuple(deck[k] for k in row) for row in idx]

    n_runouts = len(runouts)
    n_valid = len(valid)
    board_values = [card.value for card in board_cards]
    board_suits = [_SUIT_INDEX[card.suit] for card in board_cards]

    # --- rank every combo on every runout, exactly once ----------------
    # Laid out with broadcasting rather than a per-(combo, runout) Python
    # loop: at cap 100 that loop is ~300k iterations and was most of what
    # this function still cost after the algorithmic win.
    combo_values = np.array([[c.card_a.value, c.card_b.value] for _i, c in valid],
                            dtype=np.int64)
    combo_suits = np.array([[_SUIT_INDEX[c.card_a.suit], _SUIT_INDEX[c.card_b.suit]]
                            for _i, c in valid], dtype=np.int64)
    run_values = np.array([[card.value for card in run] for run in runouts],
                          dtype=np.int64).reshape(n_runouts, remaining_needed if remaining_needed > 0 else 0)
    run_suits = np.array([[_SUIT_INDEX[card.suit] for card in run] for run in runouts],
                         dtype=np.int64).reshape(n_runouts, remaining_needed if remaining_needed > 0 else 0)

    values = np.empty((n_valid, n_runouts, 7), dtype=np.int64)
    suits = np.empty((n_valid, n_runouts, 7), dtype=np.int64)
    values[:, :, 0:2] = combo_values[:, None, :]
    suits[:, :, 0:2] = combo_suits[:, None, :]
    n_board = len(board_cards)
    values[:, :, 2:2 + n_board] = np.array(board_values, dtype=np.int64)[None, None, :]
    suits[:, :, 2:2 + n_board] = np.array(board_suits, dtype=np.int64)[None, None, :]
    if remaining_needed > 0:
        values[:, :, 2 + n_board:] = run_values[None, :, :]
        suits[:, :, 2 + n_board:] = run_suits[None, :, :]
    ranks = best_hand_rank_batch(values.reshape(-1, 7),
                                 suits.reshape(-1, 7)).reshape(n_valid, n_runouts)

    # --- card-membership matrices, so no pair is examined in Python ----
    # A card index is 4 * value + suit; two hands "block" each other when
    # they share any card, and a runout is unusable for a combo on the
    # same test. Both reduce to one boolean matrix product each.
    def _card_bits(values_arr, suits_arr):
        bits = np.zeros((values_arr.shape[0], 52 * 4), dtype=bool)
        idx = values_arr * 4 + suits_arr
        for col in range(idx.shape[1]):
            bits[np.arange(idx.shape[0]), idx[:, col]] = True
        return bits

    combo_bits = _card_bits(combo_values, combo_suits)
    if remaining_needed > 0:
        run_bits = _card_bits(run_values, run_suits)
        collides = (combo_bits.astype(np.int8) @ run_bits.astype(np.int8).T) > 0
    else:
        collides = np.zeros((n_valid, n_runouts), dtype=bool)
    usable = ~collides
    blocked = (combo_bits.astype(np.int8) @ combo_bits.astype(np.int8).T) > 0

    # --- every pair is now integer comparisons over the shared runouts --
    # One vectorised pass per ROW against all later rows, rather than a
    # Python iteration per PAIR (~450k of them at cap 100).
    usable_i8 = usable.astype(np.int8)
    for a_pos in range(n_valid - 1):
        i, _combo_i = valid[a_pos]
        rank_i = ranks[a_pos]
        rest_ranks = ranks[a_pos + 1:]
        mask = usable[a_pos] & usable[a_pos + 1:]
        drawn = mask.sum(axis=1)
        wins = np.count_nonzero((rank_i > rest_ranks) & mask, axis=1)
        ties = np.count_nonzero((rank_i == rest_ranks) & mask, axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            equity = (wins + 0.5 * ties) / drawn
        # A pair sharing a card is an impossible matchup, and a pair whose
        # every shared runout collided has nothing to average — both stay
        # NaN, which is the contract the caller's nan_to_num expects.
        equity[drawn == 0] = np.nan
        equity[blocked[a_pos, a_pos + 1:]] = np.nan
        cols = np.array([j for j, _c in valid[a_pos + 1:]], dtype=np.int64)
        table[i, cols] = equity
        table[cols, i] = 1.0 - equity
    return table


def build_board_equity_table(
    board: tuple,
    combos: list,
    samples: int = DEFAULT_BOARD_EQUITY_SAMPLES,
    rng: random.Random | None = None,
    pair_rows: tuple | None = None,
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
    # M132: the generator is re-seeded PER ROW, from a base drawn off
    # `rng`, rather than advancing once across the whole triangle.
    #
    # One stream for the whole table makes each pair's draw depend on how
    # many pairs were processed before it, so computing rows 4..8 alone
    # gives different numbers than computing rows 0..12 and reading the
    # middle. Per-row seeding makes a row's values a function of the row
    # and nothing else, which is what lets one table be split across
    # workers and merged bit-identically — and keeps a sequential build
    # and a parallel one returning the same table rather than merely
    # equivalent ones.
    _row_seed_base = rng.getrandbits(63)

    # Combos blocked by the board itself can never win a share of this
    # table — every row/column for them stays NaN, exactly like i == j.
    valid = [(i, combo) for i, combo in enumerate(combos) if not combo.blocks(board_set)]

    # M132: `pair_rows` limits which rows of the upper triangle this call
    # computes, so one table can be built by several workers and merged.
    #
    # Row `a_pos` owns the pairs (a_pos, a_pos+1..n), and every row's set
    # is disjoint from every other's, so slices never overlap and a merge
    # is just "take whichever side is not NaN". A slice is otherwise
    # identical to the full build — same seeds, same values — which is
    # what makes parallel and sequential results bit-identical rather
    # than merely close. None means the whole triangle, as before.
    row_start, row_stop = pair_rows if pair_rows is not None else (0, len(valid))

    for a_pos, (i, combo_i) in enumerate(valid):
        if not (row_start <= a_pos < row_stop):
            continue
        np_rng = np.random.default_rng(_row_seed_base + a_pos)
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
