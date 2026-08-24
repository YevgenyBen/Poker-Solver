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

import numpy as np

from .cfr import _terminal_value_matrix
from .game_tree import DecisionNode, TerminalNode


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
