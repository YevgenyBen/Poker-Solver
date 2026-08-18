"""The 169 canonical Texas Hold'em starting-hand classes.

Rather than enumerating all 1326 two-card combos, preflop strategy is
conventionally expressed over the 169 canonical classes: 13 pocket pairs,
78 suited hands, and 78 offsuit hands (13 + 78 + 78 = 169). Each class is
weighted by how many concrete combos it represents (a pair like AA has 6
combos, a suited hand like AKs has 4, an offsuit hand like AKo has 12;
6*13 + 4*78 + 12*78 = 1326, the full 52-choose-2 hand count).

Note: treating each class as a single unit (rather than weighting by exact
combo-vs-combo matchups) ignores card-removal ("blocker") effects between
two players' hands. This is a standard, documented approximation for a
preflop-only solver — see the project plan for the tradeoff discussion.
"""

from dataclasses import dataclass

from .cards import Card
from .hand_utils import RANK_ORDER, rank_value

PAIR_COMBOS = 6
SUITED_COMBOS = 4
OFFSUIT_COMBOS = 12
TOTAL_COMBOS = 1326  # 52 choose 2


@dataclass(frozen=True)
class StartingHand:
    """A canonical starting-hand class, e.g. AA, AKs, or 72o.

    `suited` is meaningless (and ignored) when `high_rank == low_rank`,
    i.e. for pocket pairs.
    """

    high_rank: str
    low_rank: str
    suited: bool = False

    def __post_init__(self):
        object.__setattr__(self, "high_rank", self.high_rank.upper())
        object.__setattr__(self, "low_rank", self.low_rank.upper())
        hi, lo = rank_value(self.high_rank), rank_value(self.low_rank)
        if hi < lo:
            raise ValueError(
                f"high_rank {self.high_rank!r} must not be lower than "
                f"low_rank {self.low_rank!r}"
            )

    @property
    def is_pair(self) -> bool:
        return self.high_rank == self.low_rank

    @property
    def combo_count(self) -> int:
        if self.is_pair:
            return PAIR_COMBOS
        return SUITED_COMBOS if self.suited else OFFSUIT_COMBOS

    @property
    def combo_weight(self) -> float:
        """This class's share of all 1326 starting-hand combos."""
        return self.combo_count / TOTAL_COMBOS

    def __str__(self) -> str:
        if self.is_pair:
            return f"{self.high_rank}{self.low_rank}"
        return f"{self.high_rank}{self.low_rank}{'s' if self.suited else 'o'}"

    def __repr__(self) -> str:
        return f"StartingHand({self.high_rank!r}, {self.low_rank!r}, suited={self.suited!r})"


def parse_starting_hand(label: str) -> StartingHand:
    """Parse a label like 'AA', 'AKs', or 'AKo' into a StartingHand."""
    label = label.strip()
    if len(label) == 2:
        r1, r2 = label[0].upper(), label[1].upper()
        if rank_value(r1) != rank_value(r2):
            raise ValueError(
                f"Two-character label {label!r} must be a pocket pair "
                "(e.g. 'AA'); use a 's'/'o' suffix for non-pairs"
            )
        return StartingHand(r1, r2, suited=False)
    if len(label) == 3:
        r1, r2, kind = label[0].upper(), label[1].upper(), label[2].lower()
        if kind not in ("s", "o"):
            raise ValueError(f"Invalid suited/offsuit suffix in {label!r}: {kind!r}")
        v1, v2 = rank_value(r1), rank_value(r2)
        if v1 == v2:
            raise ValueError(f"Pocket pair {label!r} cannot have an 's'/'o' suffix")
        high, low = (r1, r2) if v1 > v2 else (r2, r1)
        return StartingHand(high, low, suited=(kind == "s"))
    raise ValueError(f"Invalid starting hand label: {label!r}")


def all_starting_hands() -> list[StartingHand]:
    """All 169 canonical starting-hand classes, highest rank first."""
    hands: list[StartingHand] = []
    ranks_high_to_low = list(reversed(RANK_ORDER))
    for rank in ranks_high_to_low:
        hands.append(StartingHand(rank, rank, suited=False))
    for i, high in enumerate(ranks_high_to_low):
        for low in ranks_high_to_low[i + 1 :]:
            hands.append(StartingHand(high, low, suited=True))
            hands.append(StartingHand(high, low, suited=False))
    return hands


def representative_combo(hand: StartingHand) -> tuple[Card, Card]:
    """One concrete pair of Cards representative of this hand class.

    Useful anywhere a real Card pair is needed (e.g. equity computation)
    but the exact suits don't matter beyond matching the class's
    suited/offsuit/pair-ness.
    """
    if hand.is_pair:
        return Card(hand.high_rank, "s"), Card(hand.low_rank, "h")
    if hand.suited:
        return Card(hand.high_rank, "s"), Card(hand.low_rank, "s")
    return Card(hand.high_rank, "s"), Card(hand.low_rank, "h")
