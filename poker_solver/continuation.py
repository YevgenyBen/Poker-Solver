"""Solved postflop continuation values for preflop terminals (M113).

M98 found the defect this exists to fix: a preflop showdown terminal is
priced `equity * pot - invested`, which is exactly right for an all-in
(the hand really does end at showdown) and wrong for every smaller bet,
which discards the postflop game that is most of a raise's value. M111
showed the same understatement explains why position is not learned — if
playing is underpriced equally at every seat, the fold/play boundary
cannot move with position. One cause, two symptoms.

M112 costed the fix. The naive shape is impossible: 6-max has 15,254
showdown terminals with money behind, and solving a flop for each is 8.3
hours. But a continuation value does not depend on HOW a terminal was
reached — only on the game that follows, i.e. the pot, the money behind
and which seats are live. Keyed that way, those 15,254 terminals collapse
to **27 distinct spots**, and the precompute is minutes rather than
hours. That is M20's canonical-library observation applied one street
earlier.

What this module provides is the primitive: given a spot, the expected
value of PLAYING it, per hand class, from a real flop solve. Nothing here
is wired into the solver — see `expected_values_at_root` for the exact
quantity, and this project's history for why wiring comes after
validation rather than before it.
"""

import math
import random

import numpy as np

from .board_equity import build_board_equity_table
from .cards import _ALL_CARDS
from .cfr import _terminal_value_matrix
from .combos import combo_class, range_from_class_frequencies
from .game_tree import DecisionNode, TerminalNode
from .solver import solve_flop
from .starting_hands import StartingHand


def expected_values_at_root(
    result,
    equity_table: np.ndarray,
    position: str,
    villain_position: str,
    villain_reach: np.ndarray | None = None,
) -> np.ndarray:
    """`position`'s expected value per hand at the root of a solved tree.

    Both sides play the AVERAGE strategy — the actual output of CFR — not
    the current one, which is a snapshot of a policy still oscillating.

    Carries a full (hero hand x villain hand) matrix down the tree rather
    than a per-hand vector, because that is what the game actually is:
    when villain acts, which action they take depends on THEIR hand, so
    collapsing villain's dimension early would average over a decision
    that has not been made yet. The matrix is contracted against
    villain's range exactly once, at the end.

    `villain_reach` defaults to uniform over villain's hands. It is
    normalized here, so callers may pass unnormalized weights.
    """
    num_hands = len(result.hands)
    if villain_reach is None:
        villain_reach = np.ones(num_hands, dtype=float)
    weights = np.asarray(villain_reach, dtype=float)
    total = weights.sum()
    if total <= 0:
        raise ValueError("villain_reach sums to zero — there is no range to solve against")
    weights = weights / total

    labels = [str(hand) for hand in result.hands]
    index = {label: i for i, label in enumerate(labels)}

    def walk(node) -> np.ndarray:
        if isinstance(node, TerminalNode):
            return _terminal_value_matrix(node, equity_table, position, villain_position)
        if not isinstance(node, DecisionNode):
            raise TypeError(f"unexpected node type in a solved tree: {type(node).__name__}")

        strategy = result.strategy_at(node)
        actor_is_hero = node.player_to_act == position
        value = np.zeros((num_hands, num_hands), dtype=float)
        for action in node.legal_actions:
            child = walk(node.children[action])
            frequencies = np.array(
                [strategy[label].get(str(action), 0.0) for label in labels], dtype=float
            )
            if actor_is_hero:
                # Hero's action depends on hero's hand -> weight ROWS.
                value += frequencies[:, None] * child
            else:
                # Villain's action depends on villain's hand -> weight COLUMNS.
                value += frequencies[None, :] * child
        return value

    return walk(result.root) @ weights


def continuation_key(pot: float, chips_behind: float, live_seats: int) -> tuple:
    """The canonical identity of a preflop terminal's FOLLOWING game.

    M112's measurement: 6-max has 15,254 showdown terminals with money
    behind, and solving a flop for each is 8.3 hours. They collapse to 27
    when keyed this way, because a continuation value does not depend on
    which action sequence reached a terminal — only on the game that
    follows. Same observation M20's canonical library rests on, one
    street earlier.

    Bucketed on log2(SPR) rather than raw SPR: postflop play changes with
    the ORDER of magnitude of the stack-to-pot ratio, not linearly with
    it. The difference between SPR 1 and 2 is a different game; the
    difference between 20 and 21 is not.
    """
    if pot <= 0:
        raise ValueError("pot must be positive to define an SPR")
    spr = max(chips_behind / pot, 1e-9)
    return (round(math.log2(spr)), live_seats)


def build_continuation_table(
    spots,
    hero_classes: dict,
    villain_classes: dict,
    boards: int = 3,
    iterations: int = 200,
    seed: int = 0,
) -> dict:
    """Solved postflop EV per hand class, per canonical spot.

    Returns {continuation_key: {hand class label: EV as a MULTIPLE OF THE
    POT}}. Normalized by pot so one solved spot serves every real
    terminal sharing its key — which is the whole point of the
    canonicalization.

    **The approximation this makes, stated plainly rather than buried.**
    The key carries SPR and live-seat count, and NOT range strength. A
    three-bet pot and a limped pot at the same SPR have very different
    ranges, and this treats them alike. That is a real fidelity cost, and
    it is unmeasured — M112 flagged exactly this as the open question and
    M100's lesson applies: a mechanism producing plausible numbers is not
    thereby correct. If validation fails, adding a range-strength
    dimension to the key is the first thing to try, not more boards.

    Boards are SAMPLED, not enumerated: 1,755 canonical flops at ~2.3s
    each per spot is not affordable, and the value being estimated is an
    average over runouts anyway.

    **Cost, measured rather than extrapolated (M115):** 27 spots x 3
    boards at a 12-class range is **1,117.6s (18.6 min)**, dominated by
    `build_board_equity_table` per board rather than by the flop solve.
    An earlier estimate of ~40s came from a 2-spot smoke test with four
    hand classes and was wrong by 28x — cost here scales with range
    width, so a fixture small enough to be fast cannot predict it.
    """
    rng = random.Random(seed)
    deck = list(_ALL_CARDS)
    table = {}

    for key, (pot, chips_behind) in spots.items():
        per_class = {}
        for board_index in range(boards):
            board = tuple(rng.sample(deck, 3))
            hero_range = range_from_class_frequencies(hero_classes, exclude=list(board))
            villain_range = range_from_class_frequencies(villain_classes, exclude=list(board))
            if not hero_range or not villain_range:
                continue
            result = solve_flop(board, hero_range, villain_range, pot=pot,
                                effective_stack_bb=chips_behind, iterations=iterations)
            # Same table and same NaN treatment `solve_flop` itself uses:
            # conflicting (hero, villain) combo pairs come back NaN, and a
            # neutral 0.5 stands in. Using a differently-prepared table
            # here would price the EV against a different game than the
            # one that was solved.
            equity_table = np.nan_to_num(
                build_board_equity_table(board, result.hands), nan=0.5
            )
            values = expected_values_at_root(result, equity_table, "OOP", "IP")
            for combo, value in zip(result.hands, values):
                label = str(combo_class(combo))
                per_class.setdefault(label, []).append(value / pot)
        table[key] = {label: sum(vs) / len(vs) for label, vs in per_class.items() if vs}
    return table
