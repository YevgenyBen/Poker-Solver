"""CFR+ (regret matching+) solving over the heads-up preflop betting tree.

The betting tree (game_tree.py) has no knowledge of hole cards — the same
tree is shared by every (BTN hand, BB hand) pair. A naive implementation
would loop over all 169x169 hand pairs and re-walk the tree for each one;
for a tree with ~100 nodes and thousands of CFR iterations that's on the
order of tens of billions of Python-level operations, far too slow.

Instead, this module walks the tree exactly once per iteration and
carries whole hand-class distributions through it as NumPy arrays:
- `reach_btn`/`reach_bb` are length-N vectors (N = number of hand
  classes), propagated *down* the tree — reach_btn[i] is the probability
  that BTN, holding hand i, plays the actions leading to this node.
- Node "values" are NxN matrices propagated *up* the tree — value[i, j]
  is BTN's expected net payoff from this node onward, given BTN holds
  hand i and BB holds hand j and both follow their current strategies.

This turns each iteration into a handful of NxN NumPy operations per
tree node instead of N*N per-pair recursions, which is what makes
solving in seconds (rather than minutes) achievable.

Reach probabilities are seeded with each hand's combo_weight (its prior
probability of being dealt), so opponent reach naturally encodes "how
likely is the opponent to hold each hand and have played to reach this
point" — see the project plan for why card-removal/blocker effects are
still ignored (each class is treated as an independent unit).
"""

from dataclasses import dataclass

import numpy as np

from .game_tree import BB, BTN, DecisionNode, TerminalNode


@dataclass
class InfoSetTable:
    """Regret/strategy accumulators for one DecisionNode, across every
    hand class the acting player could hold at that node."""

    regret_sum: np.ndarray
    strategy_sum: np.ndarray

    @classmethod
    def zeros(cls, num_hands: int, num_actions: int) -> "InfoSetTable":
        return cls(
            regret_sum=np.zeros((num_hands, num_actions)),
            strategy_sum=np.zeros((num_hands, num_actions)),
        )

    def current_strategy(self) -> np.ndarray:
        """Regret-matching+ strategy: shape (num_hands, num_actions)."""
        positive = np.maximum(self.regret_sum, 0.0)
        totals = positive.sum(axis=1, keepdims=True)
        num_actions = self.regret_sum.shape[1]
        uniform = np.full_like(self.regret_sum, 1.0 / num_actions)
        with np.errstate(invalid="ignore", divide="ignore"):
            normalized = positive / totals
        return np.where(totals > 0, normalized, uniform)

    def average_strategy(self) -> np.ndarray:
        """The actual CFR output: the time-averaged strategy."""
        totals = self.strategy_sum.sum(axis=1, keepdims=True)
        num_actions = self.strategy_sum.shape[1]
        uniform = np.full_like(self.strategy_sum, 1.0 / num_actions)
        with np.errstate(invalid="ignore", divide="ignore"):
            normalized = self.strategy_sum / totals
        return np.where(totals > 0, normalized, uniform)


def _terminal_value_matrix(node: TerminalNode, equity_table: np.ndarray) -> np.ndarray:
    """BTN's net payoff matrix (shape num_hands x num_hands) at a leaf."""
    if node.folded_player == BTN:
        return np.full(equity_table.shape, -node.btn_invested)
    if node.folded_player == BB:
        return np.full(equity_table.shape, node.bb_invested)
    return equity_table * node.pot - node.btn_invested


def _solve_recurse(
    node,
    reach_btn: np.ndarray,
    reach_bb: np.ndarray,
    updating_player: str,
    node_data: dict,
    equity_table: np.ndarray,
) -> np.ndarray:
    """Returns this node's value matrix (BTN's payoff, shape NxN)."""
    if isinstance(node, TerminalNode):
        return _terminal_value_matrix(node, equity_table)

    num_hands = equity_table.shape[0]
    actions = node.legal_actions
    table = node_data.setdefault(id(node), InfoSetTable.zeros(num_hands, len(actions)))
    strategy = table.current_strategy()
    acting_is_btn = node.player_to_act == BTN

    child_values = []
    for a_idx, action in enumerate(actions):
        child = node.children[action]
        if acting_is_btn:
            child_value = _solve_recurse(
                child, reach_btn * strategy[:, a_idx], reach_bb, updating_player, node_data, equity_table
            )
        else:
            child_value = _solve_recurse(
                child, reach_btn, reach_bb * strategy[:, a_idx], updating_player, node_data, equity_table
            )
        child_values.append(child_value)

    node_value = np.zeros((num_hands, num_hands))
    for a_idx, child_value in enumerate(child_values):
        if acting_is_btn:
            node_value += strategy[:, a_idx][:, None] * child_value
        else:
            node_value += strategy[:, a_idx][None, :] * child_value

    if node.player_to_act == updating_player:
        acting_reach = reach_btn if acting_is_btn else reach_bb
        opponent_reach = reach_bb if acting_is_btn else reach_btn

        cf_action_values = np.zeros((num_hands, len(actions)))
        for a_idx, child_value in enumerate(child_values):
            if acting_is_btn:
                cf_action_values[:, a_idx] = child_value @ opponent_reach
            else:
                cf_action_values[:, a_idx] = (-child_value).T @ opponent_reach
        cf_node_value = (
            node_value @ opponent_reach if acting_is_btn else (-node_value).T @ opponent_reach
        )

        regret = cf_action_values - cf_node_value[:, None]
        table.regret_sum = np.maximum(table.regret_sum + regret, 0.0)  # CFR+: floor at 0
        table.strategy_sum += acting_reach[:, None] * strategy

    return node_value


def solve(
    root: DecisionNode,
    hands: list,
    equity_table: np.ndarray,
    iterations: int = 1000,
) -> dict:
    """Run `iterations` of CFR+ over `root`, for the given `hands`.

    `equity_table` must be shaped (len(hands), len(hands)) with rows/cols
    in the same order as `hands` (see equity.get_equity_table).

    Returns a dict of {id(DecisionNode): InfoSetTable}. There's no
    randomness anywhere in this process (the tree is walked exhaustively,
    not sampled), so results are exactly deterministic for a given
    (root, hands, equity_table, iterations).
    """
    if equity_table.shape != (len(hands), len(hands)):
        raise ValueError(
            f"equity_table shape {equity_table.shape} doesn't match "
            f"len(hands)={len(hands)}"
        )
    reach_weights = np.array([hand.combo_weight for hand in hands])
    node_data: dict = {}
    for iteration in range(iterations):
        updating_player = BTN if iteration % 2 == 0 else BB
        _solve_recurse(
            root, reach_weights.copy(), reach_weights.copy(), updating_player, node_data, equity_table
        )
    return node_data
