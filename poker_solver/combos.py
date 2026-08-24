"""Concrete two-card starting hands ("combos"), as opposed to the 169
abstract classes `starting_hands.StartingHand` models.

Preflop deliberately treats every combo within a class as interchangeable
(see equity.py's module docstring: "does not weight by exact combo-vs-combo
blocker effects") — a defensible approximation there, since blocker
effects are a relatively minor consideration before any community cards
are known. Postflop, blocker effects are a first-order consideration
(does my range block the nut flush draw, does this specific ace block
villain's top pair) — a large part of what makes postflop strategy
different from preflop at all. So Phase C (postflop) reasons about
concrete combos once a board exists, not classes — this module is the
bridge between the two.
"""

from dataclasses import dataclass
from itertools import combinations

from .cards import SUITS, Card, parse_cards
from .equity import _suit_pairs_for
from .hand_utils import RANK_ORDER
from .starting_hands import StartingHand


@dataclass(frozen=True)
class HandCombo:
    """One concrete two-card hand, e.g. Ah Kh.

    Order-independent: HandCombo(a, b) == HandCombo(b, a) always, since a
    hand is an unordered pair of cards — normalized in __post_init__ the
    same way Card.__post_init__ normalizes rank/suit case, so equality
    and hashing never depend on construction order.
    """

    card_a: Card
    card_b: Card

    def __post_init__(self):
        card_a, card_b = self.card_a, self.card_b
        if (card_b.value, card_b.suit) > (card_a.value, card_a.suit):
            card_a, card_b = card_b, card_a
        # Swap via local variables, not sequential object.__setattr__ calls —
        # writing card_a first would overwrite it before card_b's assignment
        # could read the original value, silently duplicating one card.
        object.__setattr__(self, "card_a", card_a)
        object.__setattr__(self, "card_b", card_b)
        if card_a == card_b:
            raise ValueError(f"HandCombo needs two distinct cards, got {card_a!r} twice")

    @property
    def cards(self) -> tuple:
        return (self.card_a, self.card_b)

    def blocks(self, cards) -> bool:
        """True if this combo uses any card in `cards` (e.g. a board, or
        another player's already-dealt combo) — so this combo could not
        physically coexist with them."""
        return self.card_a in cards or self.card_b in cards

    def __str__(self) -> str:
        return f"{self.card_a}{self.card_b}"

    def __repr__(self) -> str:
        return f"HandCombo({self.card_a!r}, {self.card_b!r})"

    @classmethod
    def from_str(cls, text: str) -> "HandCombo":
        """Parse a 4-character combo string like 'AhKh' into a HandCombo."""
        parsed = parse_cards(text)
        if len(parsed) != 2:
            raise ValueError(f"HandCombo needs exactly 2 cards, got {len(parsed)} from {text!r}")
        return cls(*parsed)


def all_combos(exclude: frozenset = frozenset()) -> list:
    """Every one of the up to 1326 (52 choose 2) concrete two-card combos,
    minus any that would use a card in `exclude` (e.g. a known board)."""
    deck = [Card(rank, suit) for rank in RANK_ORDER for suit in SUITS if Card(rank, suit) not in exclude]
    return [HandCombo(a, b) for a, b in combinations(deck, 2)]


def combos_for_class(hand: StartingHand, exclude: frozenset = frozenset()) -> list:
    """A starting-hand class's concrete combos (4 suited / 12 offsuit / 6
    pair), minus any blocked by `exclude`.

    Reuses equity.py's `_suit_pairs_for` rather than re-deriving suit
    enumeration, and dedupes via HandCombo's order-independent equality:
    `_suit_pairs_for`'s (s1, s2) enumeration produces each *pair* combo
    twice (e.g. (c, d) and (d, c) are the same two physical cards for a
    pair, since both positions hold the same rank) but each suited/offsuit
    combo only once (order there encodes which rank got which suit, so
    swapped-suit orderings are genuinely different physical cards).
    """
    seen = set()
    result = []
    for suit_a, suit_b in _suit_pairs_for(hand):
        card_a = Card(hand.high_rank, suit_a)
        card_b = Card(hand.low_rank, suit_b)
        if card_a == card_b or card_a in exclude or card_b in exclude:
            continue
        combo = HandCombo(card_a, card_b)
        if combo not in seen:
            seen.add(combo)
            result.append(combo)
    return result


def range_from_class_frequencies(class_freqs: dict, exclude: frozenset = frozenset()) -> dict:
    """Bridges a preflop solve's per-class continue-frequency into a
    postflop range.

    `class_freqs` maps StartingHand -> frequency (e.g. each class's
    "didn't fold" probability from a StrategyResult) — deliberately keyed
    by StartingHand objects, not display-string labels like "AKs", so
    this doesn't need to re-parse strings back into hand classes (that
    parser was removed as dead code earlier in this project; callers
    already have the StartingHand objects a StrategyResult was solved
    with).

    Each of a class's remaining unblocked combos gets that class's
    frequency directly — the per-combo probability of continuing. A class
    entirely blocked by `exclude` (every one of its combos uses an
    excluded card) contributes nothing to the result, not a crash.

    **The class's total mass therefore scales with how many combos it
    has, and that is the point (M119, audit round 12).** This used to
    divide the frequency across the class's combos, giving every class
    the same total mass regardless of size. Two things were wrong with
    that, and both are visible without solving anything:

      * With EVERY class continuing at 1.0 — nobody has folded anything,
        so the range is the whole deck — the result was not uniform. A
        suited combo came back at 0.25 and an offsuit one at 0.0833, so
        the model believed AhKh was **three times as likely as AhKs**.
        Pairs were 2x. The whole deck is uniform by definition.
      * Blockers were cancelled exactly. AA kept total mass 1.0 whether
        6, 3, or 1 of its combos survived the board — on a two-ace board
        the single remaining combo carried the weight of all six.
        Card-removal is the stated reason postflop works in combos at
        all, and this step undid it.

    `derive_ranges_from_path` returns CONDITIONAL frequencies —
    P(took this line | holding this class), 1.0 for a position that has
    not acted — and the prior over concrete combos is uniform, so the
    posterior weight of a combo is just its class's frequency. Nothing
    to divide.

    Every concrete combo belongs to exactly one class (e.g. "AhKh" is
    only ever AKs, never AKo or any other class), so this never needs to
    merge contributions from two different classes onto the same combo —
    each combo in the result is set once, not accumulated.
    """
    range_: dict = {}
    for hand, freq in class_freqs.items():
        if freq <= 0:
            continue
        combos = combos_for_class(hand, exclude=exclude)
        if not combos:
            continue
        for combo in combos:
            range_[combo] = freq
    return range_


def combo_class(combo: "HandCombo") -> StartingHand:
    """The StartingHand class a concrete HandCombo belongs to.

    Moved here from `api/solving.py` in M114, which needed the same
    mapping in the engine and must not import from `api/` (the package
    boundary is enforced by tests/test_package_boundary.py). The API's
    `_combo_to_class` now delegates here rather than keeping a second
    copy.

    Safe because HandCombo.__post_init__ (M10) normalizes card_a to the
    higher (value, suit) pair, so card_a.rank is always the high rank. No
    pair special-case is needed: two distinct cards of the same rank must
    differ in suit, so `suited` is already False for every pair.
    """
    return StartingHand(
        combo.card_a.rank,
        combo.card_b.rank,
        suited=combo.card_a.suit == combo.card_b.suit,
    )
