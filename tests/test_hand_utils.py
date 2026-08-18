import pytest

from poker_solver.hand_utils import rank_value


def test_rank_value_low_card():
    assert rank_value("2") == 0


def test_rank_value_ace_is_highest():
    assert rank_value("A") == 12


def test_rank_value_is_case_insensitive():
    assert rank_value("t") == rank_value("T")


def test_rank_value_invalid_rank_raises():
    with pytest.raises(ValueError):
        rank_value("X")


# NOTE: compare_ranks() has no test coverage yet.
