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
