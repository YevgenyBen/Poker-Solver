import pytest

from poker_solver.starting_hands import (
    StartingHand,
    all_starting_hands,
    parse_starting_hand,
    representative_combo,
)


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


def test_parse_pair():
    hand = parse_starting_hand("AA")
    assert hand == StartingHand("A", "A")


def test_parse_suited():
    hand = parse_starting_hand("AKs")
    assert hand == StartingHand("A", "K", suited=True)


def test_parse_offsuit():
    hand = parse_starting_hand("AKo")
    assert hand == StartingHand("A", "K", suited=False)


def test_parse_reorders_ranks():
    assert parse_starting_hand("2As") == StartingHand("A", "2", suited=True)


@pytest.mark.parametrize("label", ["AA", "AKs", "AKo", "72o", "TT", "2As"])
def test_parse_round_trip(label):
    hand = parse_starting_hand(label)
    # str() always normalizes to high-rank-first ordering.
    assert parse_starting_hand(str(hand)) == hand


def test_parse_pair_with_suffix_raises():
    with pytest.raises(ValueError):
        parse_starting_hand("AAs")


def test_parse_invalid_suffix_raises():
    with pytest.raises(ValueError):
        parse_starting_hand("AKx")


def test_parse_invalid_rank_raises():
    with pytest.raises(ValueError):
        parse_starting_hand("XKs")


def test_parse_invalid_length_raises():
    with pytest.raises(ValueError):
        parse_starting_hand("A")


def test_representative_combo_pair_has_different_suits():
    card1, card2 = representative_combo(StartingHand("A", "A"))
    assert card1.rank == card2.rank == "A"
    assert card1.suit != card2.suit


def test_representative_combo_suited_matches_suits():
    card1, card2 = representative_combo(StartingHand("A", "K", suited=True))
    assert card1.suit == card2.suit


def test_representative_combo_offsuit_differs():
    card1, card2 = representative_combo(StartingHand("A", "K", suited=False))
    assert card1.suit != card2.suit
