"""Small helper functions for working with card ranks.

This module is an early scaffold for the poker solver. It currently
only handles rank parsing and comparison; suit logic and full hand
evaluation will be added later.
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


def compare_ranks(rank_a: str, rank_b: str) -> int:
    """Compare two card ranks.

    Returns -1 if `rank_a` is lower than `rank_b`, 1 if it's higher,
    and 0 if they're equal.
    """
    value_a = rank_value(rank_a)
    value_b = rank_value(rank_b)
    if value_a < value_b:
        return -1
    if value_a > value_b:
        return 1
    return 0
