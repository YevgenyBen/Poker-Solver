import itertools

import pytest

from poker_solver.canonicalize import (
    DEFAULT_STACK_BUCKET_BB,
    canonical_stack_depth,
    canonicalize_board,
    invert_suit_map,
    translate_card,
    translate_cards,
    translate_combo,
)
from poker_solver.cards import SUITS, Card
from poker_solver.combos import HandCombo


def cards(text: str) -> list:
    return [Card.from_str(token) for token in text.split()]


# ---------------------------------------------------------------------------
# canonicalize_board
# ---------------------------------------------------------------------------


def test_isomorphic_paired_boards_collapse_to_the_same_canonical_form():
    # 2c 2h 3c and 2c 2h 3h are genuinely suit-isomorphic (swap c<->h:
    # {2c,2h,3c} -> {2h,2c,3h} = {2c,2h,3h}) — the concrete counterexample
    # that sank the originally-sketched "walk in dealt order, first new
    # suit seen gets the next canonical letter" algorithm. That naive
    # walk is sensitive to which of the two paired 2s comes first, even
    # though that's a strategically meaningless accident of listing
    # order. This test is the load-bearing regression guard for having
    # picked the 24-permutation-search algorithm instead.
    board_a = tuple(cards("2c 2h 3c"))
    board_b = tuple(cards("2c 2h 3h"))
    canonical_a, _ = canonicalize_board(board_a)
    canonical_b, _ = canonicalize_board(board_b)
    assert canonical_a == canonical_b


def test_non_isomorphic_boards_canonicalize_differently():
    # A rainbow board (3 distinct suits) and a monotone board (1 suit)
    # have genuinely different suit-repetition structure — no suit
    # relabeling turns one into the other, so they must land on
    # different canonical forms.
    rainbow = tuple(cards("2c 7d 9h"))
    monotone = tuple(cards("2c 7c 9c"))
    canonical_rainbow, _ = canonicalize_board(rainbow)
    canonical_monotone, _ = canonicalize_board(monotone)
    assert canonical_rainbow != canonical_monotone


def test_hand_verifiable_rainbow_board_and_unused_suit_hole_card_translation():
    # A rainbow, no-rank-tie board: first new suit seen (h) -> c, second
    # (s) -> d, third (d) -> h; the 4th, unused-on-this-board suit (c)
    # is deterministically assigned the one remaining canonical letter
    # (s), since suit_map must be a total bijection over all 4 suits.
    board = tuple(cards("2h 7s 9d"))
    canonical_board, suit_map = canonicalize_board(board)

    assert canonical_board == tuple(cards("2c 7d 9h"))
    assert suit_map == {"h": "c", "s": "d", "d": "h", "c": "s"}

    # Ac Kc uses suit 'c', which never appears on this board — exercises
    # the "total over all 4 suits" guarantee, not just the board's own
    # cards.
    hero_hand = HandCombo(*cards("Ac Kc"))
    translated = translate_combo(hero_hand, suit_map)
    assert translated == HandCombo(*cards("As Ks"))


def test_suit_map_is_total_over_all_four_suits_even_for_a_rainbow_board():
    # A 3-card rainbow flop only ever touches 3 of the 4 real suits —
    # suit_map must still cover the 4th (unused) suit deterministically,
    # since a hero/villain hole card can use it.
    board = tuple(cards("2c 7d 9h"))
    _, suit_map = canonicalize_board(board)
    assert set(suit_map.keys()) == set(SUITS)
    assert set(suit_map.values()) == set(SUITS)  # a bijection, not just total


def test_canonicalize_board_is_independent_of_input_card_order():
    # The literal same physical board, listed in a different order,
    # must still canonicalize to the same form — necessary for this
    # module's purpose (maximizing a future library's hit rate), not
    # just a nicety, per the module's own docstring.
    board = tuple(cards("2h 7s 9d"))
    canonical_reference, _ = canonicalize_board(board)
    for permuted in itertools.permutations(board):
        canonical_permuted, _ = canonicalize_board(tuple(permuted))
        assert canonical_permuted == canonical_reference


def test_canonicalize_board_matches_the_known_flop_isomorphism_count():
    # Exhaustive, not sampled: every one of the 22,100 possible 3-card
    # boards, canonicalized. The distinct-form count is a well-known
    # combinatorial figure for suit-isomorphism-reduced flops — asserted
    # here as ground truth the shipped algorithm must reproduce exactly,
    # not just "some smaller number." Cheap (~0.5-1s): 24 permutations x
    # a tiny sort per board, no CFR/equity involved anywhere.
    deck = [Card(rank, suit) for rank in "23456789TJQKA" for suit in SUITS]
    seen = set()
    for combo in itertools.combinations(deck, 3):
        canonical_board, _ = canonicalize_board(combo)
        seen.add(canonical_board)
    assert len(seen) == 1755


def test_canonicalize_board_matches_the_known_turn_isomorphism_count():
    # Same idea, one street deeper (270,725 total 4-card boards) — a
    # deliberate, small, one-time addition to full-suite runtime
    # (~7s), included because it's still cheap enough to be worth
    # asserting as a permanent regression guard (river's ~70s cost is
    # not — see CLAUDE.md for that measurement, run once, not asserted
    # here).
    deck = [Card(rank, suit) for rank in "23456789TJQKA" for suit in SUITS]
    seen = set()
    for combo in itertools.combinations(deck, 4):
        canonical_board, _ = canonicalize_board(combo)
        seen.add(canonical_board)
    assert len(seen) == 16432


# ---------------------------------------------------------------------------
# translate_card / translate_cards / invert_suit_map
# ---------------------------------------------------------------------------


def test_translate_cards_preserves_input_order():
    suit_map = {"c": "d", "d": "h", "h": "s", "s": "c"}
    board = tuple(cards("2c 7d 9h"))
    translated = translate_cards(board, suit_map)
    assert translated == tuple(cards("2d 7h 9s"))


def test_round_trip_through_invert_suit_map_recovers_the_original_card():
    board = tuple(cards("2h 7s 9d"))
    _, suit_map = canonicalize_board(board)
    inverse = invert_suit_map(suit_map)

    # Includes a card whose suit ('c') isn't on the board at all —
    # exercising the total (not just board-restricted) part of the map.
    for card in cards("2h 7s 9d Ac"):
        canonical_card = translate_card(card, suit_map)
        assert translate_card(canonical_card, inverse) == card


# ---------------------------------------------------------------------------
# canonical_stack_depth
# ---------------------------------------------------------------------------


def test_canonical_stack_depth_leaves_an_exact_multiple_unchanged():
    assert canonical_stack_depth(100.0, bucket_bb=5.0) == pytest.approx(100.0)


def test_canonical_stack_depth_rounds_a_clear_non_halfway_value():
    assert canonical_stack_depth(102.0, bucket_bb=5.0) == pytest.approx(100.0)


def test_canonical_stack_depth_never_exceeds_the_real_stack():
    """The invariant the whole product rests on (M95).

    Every action size in a solved tree is derived from the depth the tree
    was built at, so a canonical depth ABOVE the player's real one makes
    the solver offer a bet they cannot make. This used to happen at the
    single most ordinary spot there is: a 100bb limped pot leaves 99bb,
    99 rounded to 100, and the advice came back `all_in:100.00`.

    Swept rather than spot-checked — the old to-nearest rounding passed
    every hand-picked example anyone had thought to write.
    """
    value = 0.1
    while value <= 400.0:
        for bucket in (1.0, 2.5, 5.0, 10.0):
            canonical = canonical_stack_depth(value, bucket_bb=bucket)
            assert canonical <= value + 1e-9, (
                f"{canonical} > {value} at bucket {bucket}: unaffordable advice"
            )
            assert canonical > 0.0, f"{value} at bucket {bucket} canonicalized to no game"
        value = round(value + 0.1, 4)


def test_canonical_stack_depth_rounds_down_rather_than_to_nearest():
    # 99 is nearer to 100 than to 95; it must still land on 95, because
    # a player holding 99bb cannot shove 100.
    assert canonical_stack_depth(99.0) == pytest.approx(95.0)
    assert canonical_stack_depth(12.5) == pytest.approx(10.0)
    assert canonical_stack_depth(17.5) == pytest.approx(15.0)


def test_canonical_stack_depth_leaves_a_sub_bucket_stack_exactly_as_it_is():
    """A bare floor sends anything under one bucket to 0.0, which is not
    a game — the tree would have no chips to bet. Clamping UP to one
    bucket was the obvious repair and it reintroduced the exact bug this
    function exists to prevent: 0.5bb behind, `all_in:5.00` offered.

    So a sub-bucket stack is used as is. It gives up canonical reuse for
    those depths, which costs almost nothing — a player with under 5bb
    behind barely has a decision — and buys an invariant with no
    exceptions.
    """
    assert canonical_stack_depth(3.0) == pytest.approx(3.0)
    assert canonical_stack_depth(0.5) == pytest.approx(0.5)
    assert canonical_stack_depth(3.0, bucket_bb=1.0) == pytest.approx(3.0)


def test_canonical_stack_depth_uses_the_documented_default_bucket():
    assert DEFAULT_STACK_BUCKET_BB == 5.0
    assert canonical_stack_depth(101.0) == canonical_stack_depth(101.0, bucket_bb=DEFAULT_STACK_BUCKET_BB)
