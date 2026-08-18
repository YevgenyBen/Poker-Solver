"""A real 52-card representation, built on top of hand_utils' rank helpers.

This module is intentionally independent of any game/solver logic — it just
models cards and a deck, so later modules (starting hand classes, equity,
and eventually postflop board cards) can all build on the same primitives.
"""

from dataclasses import dataclass
import random

from .hand_utils import rank_value, RANK_ORDER

SUITS = "cdhs"  # clubs, diamonds, hearts, spades


@dataclass(frozen=True)
class Card:
    """A single playing card, e.g. Card('A', 's') for the ace of spades."""

    rank: str
    suit: str

    def __post_init__(self):
        object.__setattr__(self, "rank", self.rank.upper())
        object.__setattr__(self, "suit", self.suit.lower())
        rank_value(self.rank)  # raises ValueError for an unknown rank
        if self.suit not in SUITS:
            raise ValueError(f"Unknown suit: {self.suit!r}")

    @property
    def value(self) -> int:
        """Numeric rank value (0-12), see hand_utils.rank_value."""
        return rank_value(self.rank)

    def __str__(self) -> str:
        return f"{self.rank}{self.suit}"

    def __repr__(self) -> str:
        return f"Card({self.rank!r}, {self.suit!r})"

    @classmethod
    def from_str(cls, text: str) -> "Card":
        """Parse a two-character card string like 'As' or 'Td'."""
        text = text.strip()
        if len(text) != 2:
            raise ValueError(f"Invalid card string: {text!r}")
        return cls(text[0], text[1])


def parse_cards(text: str) -> list:
    """Parse a string of concatenated two-character card codes, e.g.
    'AhKh' (2 cards) or 'Ts9h2c' (3 cards, a flop board), into a list of
    Cards. Used by the API layer and HandCombo.from_str — a single
    shared parser rather than one per caller.
    """
    text = text.strip()
    if len(text) % 2 != 0:
        raise ValueError(f"Invalid card string: {text!r} (must have an even number of characters)")
    return [Card.from_str(text[i : i + 2]) for i in range(0, len(text), 2)]


class Deck:
    """A standard 52-card deck."""

    def __init__(self):
        self.cards = [Card(rank, suit) for rank in RANK_ORDER for suit in SUITS]

    def __len__(self) -> int:
        return len(self.cards)

    def __iter__(self):
        return iter(self.cards)

    def shuffle(self, rng: random.Random | None = None) -> None:
        (rng or random).shuffle(self.cards)

    def draw(self, count: int = 1) -> list[Card]:
        """Remove and return `count` cards from the top of the deck."""
        if count > len(self.cards):
            raise ValueError("Not enough cards left in deck")
        drawn, self.cards = self.cards[:count], self.cards[count:]
        return drawn
