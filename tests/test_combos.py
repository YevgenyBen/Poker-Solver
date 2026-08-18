import pytest

from poker_solver.cards import Card
from poker_solver.combos import HandCombo, all_combos, combos_for_class, range_from_class_frequencies
from poker_solver.starting_hands import StartingHand


def test_hand_combo_is_order_independent():
    a, b = Card("A", "h"), Card("K", "h")
    assert HandCombo(a, b) == HandCombo(b, a)
    assert hash(HandCombo(a, b)) == hash(HandCombo(b, a))


def test_hand_combo_rejects_the_same_card_twice():
    a = Card("A", "h")
    with pytest.raises(ValueError):
        HandCombo(a, a)


def test_hand_combo_cards_property():
    a, b = Card("A", "h"), Card("K", "h")
    combo = HandCombo(a, b)
    assert set(combo.cards) == {a, b}


def test_hand_combo_blocks():
    a, b = Card("A", "h"), Card("K", "h")
    combo = HandCombo(a, b)
    assert combo.blocks(frozenset([a]))
    assert combo.blocks(frozenset([b]))
    assert not combo.blocks(frozenset([Card("Q", "h")]))


def test_hand_combo_str():
    combo = HandCombo(Card("A", "h"), Card("K", "h"))
    assert str(combo) in {"AhKh", "KhAh"}  # order-normalized, either is fine as long as it's stable
    assert str(combo) == str(HandCombo(Card("K", "h"), Card("A", "h")))


def test_hand_combo_from_str():
    assert HandCombo.from_str("AhKh") == HandCombo(Card("A", "h"), Card("K", "h"))


def test_hand_combo_from_str_rejects_wrong_length():
    with pytest.raises(ValueError):
        HandCombo.from_str("Ah")
    with pytest.raises(ValueError):
        HandCombo.from_str("AhKhQh")


def test_all_combos_count_with_no_exclusion():
    assert len(all_combos()) == 1326


def test_all_combos_no_duplicates():
    combos = all_combos()
    assert len(set(combos)) == len(combos)


def test_all_combos_respects_exclusion():
    excluded = frozenset([Card("A", "h")])
    combos = all_combos(exclude=excluded)
    assert len(combos) == 1326 - 51  # every combo that would have used Ah is gone
    assert all(not combo.blocks(excluded) for combo in combos)


def test_combos_for_class_counts_match_combo_count():
    assert len(combos_for_class(StartingHand("A", "A"))) == 6
    assert len(combos_for_class(StartingHand("A", "K", suited=True))) == 4
    assert len(combos_for_class(StartingHand("A", "K", suited=False))) == 12


def test_combos_for_class_no_duplicates():
    for hand in [StartingHand("A", "A"), StartingHand("A", "K", suited=True), StartingHand("A", "K", suited=False)]:
        combos = combos_for_class(hand)
        assert len(set(combos)) == len(combos)


def test_combos_for_class_respects_exclusion():
    # Excluding one ace removes exactly the combos that would have used it.
    unblocked = combos_for_class(StartingHand("A", "A"))
    blocked = combos_for_class(StartingHand("A", "A"), exclude=frozenset([Card("A", "h")]))
    assert len(blocked) == len(unblocked) - 3  # Ah paired with each of the other 3 aces is gone


def test_combos_for_class_fully_blocked_returns_empty_not_crash():
    all_aces = frozenset([Card("A", suit) for suit in "cdhs"])
    assert combos_for_class(StartingHand("A", "A"), exclude=all_aces) == []


def test_range_from_class_frequencies_weights_sum_to_input_frequency():
    freqs = {StartingHand("A", "A"): 0.9}
    range_ = range_from_class_frequencies(freqs)
    assert sum(range_.values()) == pytest.approx(0.9)
    assert len(range_) == 6  # spread across all 6 unblocked AA combos


def test_range_from_class_frequencies_skips_nonpositive_frequencies():
    freqs = {StartingHand("A", "A"): 0.0, StartingHand("K", "K"): 0.5}
    range_ = range_from_class_frequencies(freqs)
    assert all(not combo.blocks(frozenset(Card(r, s) for r in "A" for s in "cdhs")) for combo in range_)
    assert sum(range_.values()) == pytest.approx(0.5)


def test_range_from_class_frequencies_handles_a_fully_blocked_class_gracefully():
    all_aces = frozenset([Card("A", suit) for suit in "cdhs"])
    freqs = {StartingHand("A", "A"): 0.9, StartingHand("K", "K"): 0.5}
    range_ = range_from_class_frequencies(freqs, exclude=all_aces)
    # AA is entirely blocked (needs 2 aces, all 4 are excluded) — contributes nothing.
    assert not any(combo.card_a.rank == "A" or combo.card_b.rank == "A" for combo in range_)
    assert sum(range_.values()) == pytest.approx(0.5)


def test_range_from_class_frequencies_each_combo_belongs_to_exactly_one_class():
    freqs = {StartingHand("A", "K", suited=True): 1.0, StartingHand("A", "K", suited=False): 1.0}
    range_ = range_from_class_frequencies(freqs)
    assert len(range_) == 4 + 12  # AKs's 4 combos and AKo's 12 combos never overlap
