"""Small helper for working with a single card rank character.

`cards.py` builds full Card/Deck objects on top of `rank_value`; actual
hand comparison lives in `hand_eval.py` (full 5-7 card poker hand
ranking), not here.
"""

RANK_ORDER = "23456789TJQKA"


def rank_value(rank: str) -> int:
    """Return the numeric value of a single card rank.

    Ranks are '2'-'9', 'T' (ten), 'J', 'Q', 'K', 'A'. '2' is the
    lowest rank (value 0) and 'A' is the highest (value 12).

    Raises:
        ValueError: if `rank` is not a recognized rank character.
    """
    rank = rank.upper()
    if rank not in RANK_ORDER:
        raise ValueError(f"Unknown rank: {rank!r}")
    return RANK_ORDER.index(rank)
