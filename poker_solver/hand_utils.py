"""Small helper for working with a single card rank character.

`cards.py` builds full Card/Deck objects on top of `rank_value`; actual
hand comparison lives in `hand_eval.py` (full 5-7 card poker hand
ranking), not here.
"""

RANK_ORDER = "23456789TJQKA"

# M67: one dict lookup instead of a membership scan plus an index scan.
# This function sat in the innermost equity-simulation loop — profiled at
# 42.5M calls and ~17.5s cumulative on a single 6-max 169-class solve, of
# which `str.index` and `str.upper` were ~7s between them. Both entries
# are pre-seeded (upper and lower case) so the common already-normalized
# call doesn't pay for `.upper()` at all.
_RANK_VALUES = {rank: index for index, rank in enumerate(RANK_ORDER)}
_RANK_VALUES.update({rank.lower(): index for index, rank in enumerate(RANK_ORDER)})


def rank_value(rank: str) -> int:
    """Return the numeric value of a single card rank.

    Ranks are '2'-'9', 'T' (ten), 'J', 'Q', 'K', 'A'. '2' is the
    lowest rank (value 0) and 'A' is the highest (value 12).

    Raises:
        ValueError: if `rank` is not a recognized rank character.
    """
    try:
        return _RANK_VALUES[rank]
    except (KeyError, TypeError):
        raise ValueError(f"Unknown rank: {rank!r}") from None
