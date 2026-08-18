import pytest

from poker_solver.starting_hands import StartingHand, all_starting_hands


def test_all_starting_hands_count():
    assert len(all_starting_hands()) == 169


def test_all_starting_hands_no_duplicates():
    hands = all_starting_hands()
    assert len(set(hands)) == len(hands)


def test_combo_weights_sum_to_1326():
    total = sum(hand.combo_count for hand in all_starting_hands())
    assert total == 1326


def test_pair_count_is_13():
    pairs = [hand for hand in all_starting_hands() if hand.is_pair]
    assert len(pairs) == 13


def test_suited_and_offsuit_counts_are_78_each():
    hands = all_starting_hands()
    suited = [h for h in hands if not h.is_pair and h.suited]
    offsuit = [h for h in hands if not h.is_pair and not h.suited]
    assert len(suited) == 78
    assert len(offsuit) == 78


def test_pair_combo_count_is_6():
    assert StartingHand("A", "A").combo_count == 6


def test_suited_combo_count_is_4():
    assert StartingHand("A", "K", suited=True).combo_count == 4


def test_offsuit_combo_count_is_12():
    assert StartingHand("A", "K", suited=False).combo_count == 12


def test_low_rank_greater_than_high_rank_raises():
    with pytest.raises(ValueError):
        StartingHand("2", "A")


def test_str_pair():
    assert str(StartingHand("A", "A")) == "AA"


def test_str_suited():
    assert str(StartingHand("A", "K", suited=True)) == "AKs"


def test_str_offsuit():
    assert str(StartingHand("A", "K", suited=False)) == "AKo"


def test_str_normalizes_rank_case():
    assert str(StartingHand("a", "k", suited=True)) == "AKs"
