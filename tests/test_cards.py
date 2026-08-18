import pytest

from poker_solver.cards import Card, Deck, SUITS


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
