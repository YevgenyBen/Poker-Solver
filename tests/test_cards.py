import pytest

from poker_solver.cards import _ALL_CARDS, Card, Deck, SUITS, parse_cards, remaining_deck


def test_deck_has_52_cards():
    assert len(Deck()) == 52


def test_deck_cards_are_unique():
    deck = Deck()
    assert len(set(deck)) == 52


def test_deck_contains_all_suits_and_ranks():
    deck = Deck()
    ranks = {card.rank for card in deck}
    suits = {card.suit for card in deck}
    assert ranks == set("23456789TJQKA")
    assert suits == set(SUITS)


def test_card_equality_and_hash():
    assert Card("A", "s") == Card("A", "s")
    assert Card("A", "s") != Card("A", "h")
    assert len({Card("A", "s"), Card("A", "s"), Card("K", "s")}) == 2


def test_card_normalizes_case():
    assert Card("a", "S") == Card("A", "s")


def test_card_value_matches_rank_order():
    assert Card("2", "s").value < Card("A", "s").value


def test_card_str():
    assert str(Card("A", "s")) == "As"


def test_card_from_str_parses():
    assert Card.from_str("Td") == Card("T", "d")


def test_card_from_str_invalid_length_raises():
    with pytest.raises(ValueError):
        Card.from_str("Ass")


def test_card_invalid_rank_raises():
    with pytest.raises(ValueError):
        Card("X", "s")


def test_card_invalid_suit_raises():
    with pytest.raises(ValueError):
        Card("A", "x")


def test_deck_draw_reduces_size():
    deck = Deck()
    drawn = deck.draw(2)
    assert len(drawn) == 2
    assert len(deck) == 50


def test_deck_draw_too_many_raises():
    deck = Deck()
    with pytest.raises(ValueError):
        deck.draw(53)


def test_deck_shuffle_keeps_all_cards():
    deck = Deck()
    original = set(deck)
    deck.shuffle()
    assert set(deck) == original
    assert len(deck) == 52


def test_parse_cards_empty_string():
    assert parse_cards("") == []


def test_parse_cards_two_cards():
    assert parse_cards("AhKh") == [Card("A", "h"), Card("K", "h")]


def test_parse_cards_flop_board():
    assert parse_cards("Ts9h2c") == [Card("T", "s"), Card("9", "h"), Card("2", "c")]


def test_parse_cards_rejects_odd_length():
    with pytest.raises(ValueError):
        parse_cards("Ah9")


def test_remaining_deck_with_no_exclusions_has_all_52():
    assert len(remaining_deck(frozenset())) == 52


def test_remaining_deck_excludes_given_cards():
    excluded = frozenset({Card("A", "s"), Card("K", "d")})
    deck = remaining_deck(excluded)
    assert len(deck) == 50
    assert Card("A", "s") not in deck
    assert Card("K", "d") not in deck


def test_remaining_deck_accepts_a_plain_list_not_just_a_set():
    deck = remaining_deck([Card("2", "c")])
    assert len(deck) == 51


def test_all_cards_has_exactly_52_unique_cards():
    assert len(_ALL_CARDS) == 52
    assert len(set(_ALL_CARDS)) == 52


def test_remaining_deck_reuses_the_shared_all_cards_objects():
    # M47: a real regression guard for the perf fix — remaining_deck used
    # to build fresh Card() instances on every call (profiled as a real
    # hot-path cost, see CLAUDE.md's M47 entry); it must now filter the
    # shared _ALL_CARDS list instead of reconstructing cards.
    deck = remaining_deck(frozenset())
    assert all(any(card is shared for shared in _ALL_CARDS) for card in deck)


def test_deck_instances_share_card_objects_but_not_the_list():
    # Deck() also reuses _ALL_CARDS (M47) — safe only because Card is
    # frozen/immutable; each Deck still gets its own independent list, so
    # draining one deck can't affect another's.
    deck_a = Deck()
    deck_b = Deck()
    assert deck_a.cards[0] is deck_b.cards[0]
    deck_a.draw(5)
    assert len(deck_a) == 47
    assert len(deck_b) == 52
