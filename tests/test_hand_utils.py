import pytest

from poker_solver.hand_utils import compare_ranks, rank_value


def test_rank_value_low_card():
    assert rank_value("2") == 0


def test_rank_value_ace_is_highest():
    assert rank_value("A") == 12


def test_rank_value_is_case_insensitive():
    assert rank_value("t") == rank_value("T")


def test_rank_value_invalid_rank_raises():
    with pytest.raises(ValueError):
        rank_value("X")


def test_compare_ranks_lower():
    assert compare_ranks("2", "A") == -1


def test_compare_ranks_higher():
    assert compare_ranks("K", "Q") == 1


def test_compare_ranks_equal():
    assert compare_ranks("9", "9") == 0


def test_compare_ranks_is_case_insensitive():
    assert compare_ranks("k", "K") == 0
