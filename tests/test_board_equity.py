import random

import numpy as np
import pytest

from poker_solver.board_equity import build_board_equity_table, two_combo_equity
from poker_solver.cards import Card
from poker_solver.combos import HandCombo


def cards(text: str) -> list:
    """Parse a space-separated string of cards, e.g. 'As Ks Qs Js Ts'."""
    return [Card.from_str(token) for token in text.split()]


def test_complete_board_pair_beats_high_card_exactly():
    # A fully-dealt (river) board makes equity exact, not sampled — no
    # runout randomness left, so this is a fully deterministic check.
    board = tuple(cards("2c 7d 9h Jc Ks"))
    aces = HandCombo(*cards("Ah Ad"))  # pairs the board -> one pair, aces
    high_card = HandCombo(*cards("3h 4h"))  # improves nothing -> king-high
    table = build_board_equity_table(board, [aces, high_card])
    assert table[0, 1] == pytest.approx(1.0)
    assert table[1, 0] == pytest.approx(0.0)


def test_complete_board_is_symmetric():
    board = tuple(cards("2c 7d 9h Jc Ks"))
    aces = HandCombo(*cards("Ah Ad"))
    high_card = HandCombo(*cards("3h 4h"))
    table = build_board_equity_table(board, [aces, high_card])
    assert table[0, 1] + table[1, 0] == pytest.approx(1.0)


def test_diagonal_and_self_blocked_entries_are_nan():
    board = tuple(cards("2c 7d 9h"))
    a = HandCombo(*cards("Ah Ad"))
    b = HandCombo(*cards("Kh Kd"))
    table = build_board_equity_table(board, [a, b])
    assert np.isnan(table[0, 0])
    assert np.isnan(table[1, 1])


def test_combos_sharing_a_card_are_nan():
    board = tuple(cards("2c 7d 9h"))
    a = HandCombo(*cards("Ah Ad"))
    b = HandCombo(*cards("Ah Kd"))  # shares Ah with `a` — impossible matchup
    table = build_board_equity_table(board, [a, b])
    assert np.isnan(table[0, 1])
    assert np.isnan(table[1, 0])


def test_combo_blocked_by_the_board_is_entirely_nan():
    board = tuple(cards("2c 7d 9h"))
    blocked = HandCombo(Card("2", "c"), Card("Q", "h"))  # 2c is already on the board
    other = HandCombo(*cards("Ah Ad"))
    table = build_board_equity_table(board, [blocked, other])
    assert np.all(np.isnan(table[0, :]))
    assert np.all(np.isnan(table[:, 0]))


def test_flop_board_equity_is_a_valid_probability_and_deterministic_given_a_seed():
    board = tuple(cards("2c 7d 9h"))
    strong = HandCombo(*cards("Ah Ad"))
    weak = HandCombo(*cards("3h 4d"))
    table_1 = build_board_equity_table(board, [strong, weak], samples=100, rng=random.Random(1))
    table_2 = build_board_equity_table(board, [strong, weak], samples=100, rng=random.Random(1))
    assert 0.0 <= table_1[0, 1] <= 1.0
    assert table_1[0, 1] == table_2[0, 1]


def test_flop_overpair_favored_over_unpaired_undercards():
    # AA on a dry, unpaired low board should be a big favorite over two
    # unconnected undercards that haven't paired or drawn to anything.
    board = tuple(cards("2c 7d 9h"))
    overpair = HandCombo(*cards("Ah Ad"))
    weak = HandCombo(*cards("3h 4d"))
    table = build_board_equity_table(board, [overpair, weak], samples=300, rng=random.Random(1))
    assert table[0, 1] > 0.8


def test_rejects_a_board_with_more_than_five_cards():
    board = tuple(cards("2c 7d 9h Jc Ks Qh"))
    with pytest.raises(ValueError):
        build_board_equity_table(board, [HandCombo(*cards("Ah Ad"))])


def test_two_combo_equity_matches_the_table_entries():
    board = tuple(cards("2c 7d 9h Jc Ks"))
    aces = HandCombo(*cards("Ah Ad"))
    high_card = HandCombo(*cards("3h 4h"))
    equity_a, equity_b = two_combo_equity(board, aces, high_card)
    assert equity_a == pytest.approx(1.0)
    assert equity_b == pytest.approx(0.0)


def test_two_combo_equity_rejects_an_impossible_matchup():
    board = tuple(cards("2c 7d 9h"))
    a = HandCombo(*cards("Ah Ad"))
    b = HandCombo(*cards("Ah Kd"))  # shares Ah with `a`
    with pytest.raises(ValueError):
        two_combo_equity(board, a, b)


# ---------------------------------------------------------------------------
# M12: a turn board (remaining_needed == 1, only the river left to come) is
# resolved exactly — every possible river enumerated, not Monte Carlo
# sampled — unlike a flop board's remaining_needed == 2, which stays sampled.
# ---------------------------------------------------------------------------


def test_turn_board_equity_is_exact_not_sampled():
    # Different samples/rng should have zero effect on the result once
    # remaining_needed == 1, since they're silently unused on that path.
    board = tuple(cards("2c 7d 9h Ks"))
    strong = HandCombo(*cards("Ah Ad"))
    weak = HandCombo(*cards("3h 4d"))
    table_1 = build_board_equity_table(board, [strong, weak], samples=10, rng=random.Random(1))
    table_2 = build_board_equity_table(board, [strong, weak], samples=999, rng=random.Random(999))
    assert table_1[0, 1] == table_2[0, 1]


def test_turn_board_equity_matches_a_hand_verifiable_value():
    # Hero holds quad aces outright (board already has three, hero's
    # other hole card is a king kicker) — the literal best possible hand,
    # beating anything villain could make on any of the 44 possible
    # rivers, including villain's own best case (quad twos, from a river
    # 2c completing three 2s already on board+in hand). Equity is exactly
    # 1.0 regardless of the river, not "close to."
    board = tuple(cards("Ac Ad Ah 2s"))
    quad_aces = HandCombo(*cards("As Ks"))
    trip_twos = HandCombo(*cards("2h 2d"))
    table = build_board_equity_table(board, [quad_aces, trip_twos])
    assert table[0, 1] == pytest.approx(1.0)
    assert table[1, 0] == pytest.approx(0.0)


def test_turn_board_equity_deterministic_with_no_rng_supplied():
    board = tuple(cards("2c 7d 9h Ks"))
    strong = HandCombo(*cards("Ah Ad"))
    weak = HandCombo(*cards("3h 4d"))
    table_1 = build_board_equity_table(board, [strong, weak])
    table_2 = build_board_equity_table(board, [strong, weak])
    assert table_1[0, 1] == table_2[0, 1]

def test_only_the_flop_table_is_sampled_which_is_why_chance_nodes_take_no_samples():
    """M154. The property a design decision rests on, pinned.

    `build_chance_node` accepts neither `equity_samples` nor
    `equity_seed`, and `solve_flop_turn` documents dropping
    `equity_samples` because "board_equity tables built here are all
    turn-board tables - remaining_needed==1 - which are resolved exactly,
    not sampled, so there's nothing to tune".

    M153 flagged that as unverified rather than guess at it. It is
    correct: `remaining_needed <= 1` enumerates every single-card runout
    and ignores `samples`/`rng` entirely. Every table a chance node
    builds is a turn board (one card to come) or a river board (none), so
    there is genuinely nothing to tune.

    If that ever stops being true - if turn boards became sampled - the
    chance-node path would silently start using library defaults for a
    quantity its callers think they control, and nothing else would say
    so.
    """
    import random

    import numpy as np

    combos = [
        HandCombo(Card.from_str("9s"), Card.from_str("9d")),
        HandCombo(Card.from_str("Qd"), Card.from_str("Qh")),
        HandCombo(Card.from_str("Ah"), Card.from_str("Kh")),
        HandCombo(Card.from_str("5s"), Card.from_str("4d")),
    ]

    def table(board_text, samples, seed):
        board = tuple(Card.from_str(board_text[i:i + 2])
                      for i in range(0, len(board_text), 2))
        return np.nan_to_num(
            build_board_equity_table(board, combos, samples=samples,
                                     rng=random.Random(seed)))

    # Flop: two cards to come, Monte Carlo — sampling must matter.
    assert not np.allclose(table("2h6d9c", 5, 0), table("2h6d9c", 400, 7)), (
        "the flop table stopped depending on its sample count — either it "
        "became exact, or sampling is being ignored where it should not be"
    )
    # Turn and river: resolved exactly, so neither knob may change them.
    assert np.allclose(table("2h6d9cKs", 5, 0), table("2h6d9cKs", 400, 7)), (
        "a TURN board table now depends on sampling — chance nodes take no "
        "sample count precisely because it did not, so they would now be "
        "silently using library defaults"
    )
    assert np.allclose(table("2h6d9cKsQh", 5, 0), table("2h6d9cKsQh", 400, 7)), (
        "a RIVER board table now depends on sampling, though a complete "
        "board has nothing left to draw"
    )


def test_shared_runouts_agree_EXACTLY_where_both_builders_enumerate():
    """M176. The shared-runout builder ranks each combo once per runout
    instead of once per pair, and drops runouts that collide with a pair's
    hole cards rather than excluding them up front.

    Dropping is rejection sampling, so the surviving draws are exactly the
    conditional distribution the per-pair builder enumerates — and on TURN
    and RIVER boards both builders enumerate rather than sample
    (`remaining_needed <= 1`, M154), so there is nothing left to differ.
    They must agree to the digit.

    This is the correctness evidence for the whole change. On FLOP boards
    the two can only agree within Monte Carlo error, so agreement there
    would prove nothing; here it proves the comparison logic, the
    collision handling and the NaN contract all match.
    """
    from poker_solver.board_equity import build_shared_runout_equity_table
    from poker_solver.combos import range_from_class_frequencies
    from poker_solver.starting_hands import all_starting_hands

    for cards in (("Kd", "7c", "2h", "Ts"), ("Kd", "7c", "2h", "Ts", "4c")):
        board = tuple(Card.from_str(c) for c in cards)
        combos = sorted(
            range_from_class_frequencies({h: 1.0 for h in all_starting_hands()[:14]},
                                         exclude=frozenset(board)),
            key=str)
        per_pair = build_board_equity_table(board, combos)
        shared = build_shared_runout_equity_table(board, combos)

        # The NaN contract must match exactly: a pair sharing a card is an
        # impossible matchup in both, and nothing else may be undefined.
        assert np.array_equal(np.isnan(per_pair), np.isnan(shared)), (
            f"{''.join(cards)}: the two builders disagree about which cells are defined")
        both = ~np.isnan(per_pair)
        assert both.any(), "no defined cells — the fixture stopped exercising anything"
        worst = float(np.abs(per_pair[both] - shared[both]).max())
        assert worst == 0.0, (
            f"{''.join(cards)}: enumerated boards must agree exactly, worst {worst:.2e}")


def test_the_shared_runout_table_is_a_valid_equity_table():
    """M176. The structural contract, on a FLOP board where the values are
    sampled and so cannot be compared cell by cell against anything.
    """
    from poker_solver.board_equity import build_shared_runout_equity_table
    from poker_solver.combos import range_from_class_frequencies
    from poker_solver.starting_hands import all_starting_hands

    board = tuple(Card.from_str(c) for c in ("Th", "5s", "7c"))
    combos = sorted(
        range_from_class_frequencies({h: 1.0 for h in all_starting_hands()[:10]},
                                     exclude=frozenset(board)),
        key=str)
    table = build_shared_runout_equity_table(board, combos, samples=64)

    defined = ~np.isnan(table)
    assert defined.any()
    assert np.all(table[defined] >= 0.0) and np.all(table[defined] <= 1.0)
    # Zero-sum: hero's equity against villain is one minus the reverse.
    pairs = defined & defined.T
    assert np.allclose(table[pairs] + table.T[pairs], 1.0, atol=1e-9)
    # A combo has no equity against itself, and none against a hand it blocks.
    assert np.all(np.isnan(np.diag(table)))
    for i, a in enumerate(combos):
        for j, b in enumerate(combos):
            if i != j and a.blocks(b.cards):
                assert np.isnan(table[i, j]), f"{a} blocks {b} but the cell is defined"


def test_shared_runouts_are_deterministic_for_a_given_seed():
    """M176. Same seed in, same table out — the property M153/F44 exists to
    protect, since a seed that cannot be varied makes every
    seed-variation convergence check vacuous.
    """
    import random

    from poker_solver.board_equity import build_shared_runout_equity_table
    from poker_solver.combos import range_from_class_frequencies
    from poker_solver.starting_hands import all_starting_hands

    board = tuple(Card.from_str(c) for c in ("Th", "5s", "7c"))
    combos = sorted(
        range_from_class_frequencies({h: 1.0 for h in all_starting_hands()[:8]},
                                     exclude=frozenset(board)),
        key=str)
    a = build_shared_runout_equity_table(board, combos, samples=48, rng=random.Random(7))
    b = build_shared_runout_equity_table(board, combos, samples=48, rng=random.Random(7))
    assert np.array_equal(np.isnan(a), np.isnan(b))
    both = ~np.isnan(a)
    assert np.array_equal(a[both], b[both]), "same seed produced a different table"

    c = build_shared_runout_equity_table(board, combos, samples=48, rng=random.Random(99))
    assert not np.array_equal(a[both], c[both]), (
        "a different seed produced an identical table — the seed is being dropped, "
        "which is exactly F44")
