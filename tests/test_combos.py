import pytest

from poker_solver.cards import Card
from poker_solver.combos import HandCombo, all_combos, combos_for_class, range_from_class_frequencies
from poker_solver.starting_hands import StartingHand, all_starting_hands


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


def test_every_combo_of_a_class_carries_that_class_frequency():
    """M119 (audit round 12). This test used to assert the opposite —
    that a class's weights SUM to its frequency, i.e. the frequency was
    divided across the class's combos. That was the defect, not the
    contract.

    A class frequency out of `derive_ranges_from_path` is CONDITIONAL:
    P(took this line | holding this class). The prior over concrete
    combos is uniform, so each combo's posterior weight is just its
    class's frequency, and a class's total mass correctly scales with
    how many of its combos exist.
    """
    freqs = {StartingHand("A", "A"): 0.9}
    range_ = range_from_class_frequencies(freqs)
    assert len(range_) == 6
    assert all(weight == pytest.approx(0.9) for weight in range_.values())
    assert sum(range_.values()) == pytest.approx(0.9 * 6)


def test_a_range_of_everything_is_the_uniform_deck():
    """M119. The check that made the defect undeniable without solving
    anything: if every class continues with probability 1, nobody has
    folded, so the range IS the whole deck — which is uniform by
    definition.

    It used to come back with 312 suited combos at 0.25, 78 pairs at
    0.1667 and 936 offsuit at 0.0833 — the model believed AhKh was three
    times as likely as AhKs.
    """
    everything = {hand: 1.0 for hand in all_starting_hands()}
    range_ = range_from_class_frequencies(everything)
    assert len(range_) == 1326
    assert set(range_.values()) == {1.0}


def test_blockers_actually_remove_mass_from_a_class():
    """M119. Card removal is the stated reason postflop works in
    concrete combos at all, and the old weighting cancelled it exactly:
    AA kept total mass 1.0 whether 6, 3, or 1 of its combos survived the
    board. On a two-ace board a single combo carried the weight of all
    six.
    """
    aa = {StartingHand("A", "A"): 1.0}
    unblocked = range_from_class_frequencies(aa)
    one_ace = range_from_class_frequencies(aa, exclude=frozenset([Card("A", "s")]))
    two_aces = range_from_class_frequencies(
        aa, exclude=frozenset([Card("A", "s"), Card("A", "d")]))

    assert (len(unblocked), len(one_ace), len(two_aces)) == (6, 3, 1)
    masses = [sum(r.values()) for r in (unblocked, one_ace, two_aces)]
    assert masses == [pytest.approx(6.0), pytest.approx(3.0), pytest.approx(1.0)]
    assert masses[0] > masses[1] > masses[2], "blockers must reduce a class's mass"


def test_range_from_class_frequencies_skips_nonpositive_frequencies():
    freqs = {StartingHand("A", "A"): 0.0, StartingHand("K", "K"): 0.5}
    range_ = range_from_class_frequencies(freqs)
    assert all(not combo.blocks(frozenset(Card(r, s) for r in "A" for s in "cdhs")) for combo in range_)
    # KK's 6 combos, each carrying KK's own frequency (M119).
    assert sum(range_.values()) == pytest.approx(0.5 * 6)


def test_range_from_class_frequencies_handles_a_fully_blocked_class_gracefully():
    all_aces = frozenset([Card("A", suit) for suit in "cdhs"])
    freqs = {StartingHand("A", "A"): 0.9, StartingHand("K", "K"): 0.5}
    range_ = range_from_class_frequencies(freqs, exclude=all_aces)
    # AA is entirely blocked (needs 2 aces, all 4 are excluded) — contributes nothing.
    assert not any(combo.card_a.rank == "A" or combo.card_b.rank == "A" for combo in range_)
    assert sum(range_.values()) == pytest.approx(0.5 * 6)  # KK's 6 combos (M119)


def test_range_from_class_frequencies_each_combo_belongs_to_exactly_one_class():
    freqs = {StartingHand("A", "K", suited=True): 1.0, StartingHand("A", "K", suited=False): 1.0}
    range_ = range_from_class_frequencies(freqs)
    assert len(range_) == 4 + 12  # AKs's 4 combos and AKo's 12 combos never overlap
