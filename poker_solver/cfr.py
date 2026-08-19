"""CFR+ solving over the N-player preflop betting tree.

Two solving paths live here, sharing InfoSetTable:

1. `solve()` — exact, exhaustive CFR+, kept as a deliberate fast path
   for any two-position tree (heads-up preflop, or a flop-only tree —
   see M11). The betting tree (game_tree.py) has no knowledge of hole
   cards — the same tree is shared by every (position_a hand, position_b
   hand) pair. A naive implementation would loop over all 169x169 hand
   pairs and re-walk the tree for each one; for a tree with ~100 nodes
   and thousands of iterations that's on the order of tens of billions
   of Python-level operations, far too slow. Instead this walks the tree
   exactly once per iteration, carrying whole hand-class distributions
   through it as NumPy arrays: `reach_a`/`reach_b` are length-N vectors
   propagated *down* the tree, and node "values" are NxN matrices
   propagated *up* it (value[i, j] = position_a's payoff given they hold
   hand i and position_b holds hand j). This turns each iteration into a
   handful of NxN array ops instead of N*N per-pair recursions. Position
   labels default to `(BTN, BB)` and reach defaults to `combo_weight`
   (preflop's original behavior, unchanged); both are overridable — see
   `solve()`'s own docstring — so a postflop tree can supply its own
   labels (`("OOP", "IP")`) and starting ranges (a real range from
   earlier action, not "every hand equally likely") without a second
   implementation.

2. `mccfr_solve()` — External-Sampling MCCFR (traverser-vectorized),
   for N>=2 players generally (used for N>=3 in practice). Exhaustive
   CFR does not survive the jump to 3+ players: the value representation
   would need to become an N-dimensional (169^N) tensor, which blows up
   in both memory and compute well before N=4 — see the project plan
   for the numbers. Instead, each iteration picks one *traverser*
   position (cycling through all players across iterations); the
   traverser's own hand stays vectorized over all 169 classes and their
   own decision nodes are explored exhaustively (regret/strategy-sum
   updates stay exact for them), while every *other* position's hole
   cards and every one of their action choices are sampled once per
   iteration from their true distributions (combo_weight for hole cards;
   current strategy, blended with a small exploration floor, for
   actions — sampled directly, with no importance-sampling correction,
   see EXPLORATION_EPSILON and _mccfr_recurse below for why). The
   exploration floor turned out to matter in practice, not just in
   theory: CFR+'s regret flooring can legitimately push
   current_strategy() to an *exact* 0/1 split even when the true
   equilibrium wants a small residual frequency (e.g. "call 0.2% of the
   time"), and sampling straight from that degenerate strategy makes the
   rare-but-consequential branch permanently unreachable — silently
   starving the traverser's value estimate of it. An importance-sampling
   correction (the textbook-unbiased fix) was tried first but rejected:
   at N>=3 it compounds multiplicatively across nested opponent decisions
   (e.g. SB's node then BB's node), producing high-variance outliers that
   CFR+'s regret flooring turns actively destructive (one bad iteration
   can erase many iterations' worth of accumulated positive regret).
   Sampling directly from the floored strategy trades textbook
   unbiasedness for a small, bounded, non-compounding bias instead — see
   _mccfr_recurse for the full writeup and the M8 PR/commit for the
   diagnosis. This was caught during M8 by cross-validating against the
   exact solver at N=2, then stress-tested at N=3 against known GTO
   preflop intuition (premium hands almost never fold) before trusting
   it. Terminal payoffs come from equity.MultiwayEquityCache rather than
   a precomputed table, since a full N-way equity table is never viable
   to build eagerly.

Reach probabilities are seeded with each hand's combo_weight (its prior
probability of being dealt) in both paths — see the project plan for why
card-removal/blocker effects are still ignored (each class is treated as
an independent unit) at any N.
"""

import random
from dataclasses import dataclass

import numpy as np

from .equity import MultiwayEquityCache
from .game_tree import BB, BTN, DecisionNode, TerminalNode

# Opponent-action sampling always retains at least this much probability
# mass on every legal action, regardless of how converged/degenerate
# current_strategy() has become — see the module docstring and
# _mccfr_recurse for why this is necessary, not just a nice-to-have.
#
# Kept deliberately small: since sampling is no longer importance-weight
# corrected (see _mccfr_recurse), each opponent decision along a path has
# an EXPLORATION_EPSILON chance of taking a "forced exploration" action
# rather than their true strategy, biasing that one sampled path. This
# compounds with tree depth (roughly 1-(1-eps)^depth chance that *some*
# node along the path was forced) — at 3-max's default 4-raise tree,
# depth-4 opponent chains are possible, so a large epsilon here measurably
# slows/biases convergence for hands whose value depends on getting deep
# into the tree correctly (empirically, 0.15 visibly hurt convergence at
# N=3 for exactly this reason; 0.05 measured meaningfully better without
# reintroducing the original degenerate-lockout bug it exists to prevent).
EXPLORATION_EPSILON = 0.05


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


def _terminal_value_matrix(
    node: TerminalNode, equity_table: np.ndarray, position_a: str, position_b: str
) -> np.ndarray:
    """`position_a`'s net payoff matrix (shape num_hands x num_hands) at a leaf."""
    if position_a in node.folded:
        return np.full(equity_table.shape, -node.invested[position_a])
    if position_b in node.folded:
        return np.full(equity_table.shape, node.pot - node.invested[position_a])
    return equity_table * node.pot - node.invested[position_a]


def _solve_recurse(
    node,
    reach_a: np.ndarray,
    reach_b: np.ndarray,
    updating_player: str,
    node_data: dict,
    equity_table: np.ndarray,
    position_a: str,
    position_b: str,
) -> np.ndarray:
    """Returns this node's value matrix (`position_a`'s payoff, shape NxN)."""
    if isinstance(node, TerminalNode):
        return _terminal_value_matrix(node, equity_table, position_a, position_b)

    num_hands = equity_table.shape[0]
    actions = node.legal_actions
    table = node_data.setdefault(id(node), InfoSetTable.zeros(num_hands, len(actions)))
    strategy = table.current_strategy()
    acting_is_a = node.player_to_act == position_a

    child_values = []
    for a_idx, action in enumerate(actions):
        child = node.children[action]
        if acting_is_a:
            child_value = _solve_recurse(
                child, reach_a * strategy[:, a_idx], reach_b, updating_player,
                node_data, equity_table, position_a, position_b,
            )
        else:
            child_value = _solve_recurse(
                child, reach_a, reach_b * strategy[:, a_idx], updating_player,
                node_data, equity_table, position_a, position_b,
            )
        child_values.append(child_value)

    node_value = np.zeros((num_hands, num_hands))
    for a_idx, child_value in enumerate(child_values):
        if acting_is_a:
            node_value += strategy[:, a_idx][:, None] * child_value
        else:
            node_value += strategy[:, a_idx][None, :] * child_value

    if node.player_to_act == updating_player:
        acting_reach = reach_a if acting_is_a else reach_b
        opponent_reach = reach_b if acting_is_a else reach_a

        cf_action_values = np.zeros((num_hands, len(actions)))
        for a_idx, child_value in enumerate(child_values):
            if acting_is_a:
                cf_action_values[:, a_idx] = child_value @ opponent_reach
            else:
                cf_action_values[:, a_idx] = (-child_value).T @ opponent_reach
        cf_node_value = (
            node_value @ opponent_reach if acting_is_a else (-node_value).T @ opponent_reach
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
    positions: tuple = (BTN, BB),
    initial_reach: dict | None = None,
) -> dict:
    """Run `iterations` of CFR+ over `root`, for the given `hands`.

    `equity_table` must be shaped (len(hands), len(hands)) with rows/cols
    in the same order as `hands` (see equity.get_equity_table).

    `positions` is the (first, second) position labels this tree uses —
    defaults to `(BTN, BB)`, preflop's convention, unchanged from before
    this parameter existed. A flop-only tree (M11) passes its own two
    position labels instead (e.g. `("OOP", "IP")` — postflop's natural
    labels, not blinds) since `root.player_to_act` values come from
    whatever `GameConfig`/`StreetConfig` built the tree, not from this
    module.

    `initial_reach` optionally maps position -> starting reach-weight
    array (same length/order as `hands`), overriding the default
    combo_weight-derived prior (each hand's raw prior probability of
    being dealt) for that position — e.g. a real range carried over from
    an earlier street's action, not "every hand equally likely to have
    continued." A position missing from `initial_reach` (or the default
    `None`, meaning "no overrides at all") still falls back to
    combo_weight, so every pre-existing preflop call site is unaffected.

    Returns a dict of {id(DecisionNode): InfoSetTable}. There's no
    randomness anywhere in this process (the tree is walked exhaustively,
    not sampled), so results are exactly deterministic for a given
    (root, hands, equity_table, iterations, positions, initial_reach).
    """
    if equity_table.shape != (len(hands), len(hands)):
        raise ValueError(
            f"equity_table shape {equity_table.shape} doesn't match "
            f"len(hands)={len(hands)}"
        )
    position_a, position_b = positions
    initial_reach = initial_reach or {}

    def _default_reach():
        # Computed lazily, not eagerly: `hands` may be combos.HandCombo
        # (M11's flop tree), which has no combo_weight at all — fine, as
        # long as every position is actually overridden by initial_reach
        # in that case, which solve_flop always does. Only preflop's
        # StartingHand-keyed calls (where combo_weight exists) ever fall
        # through to this.
        return np.array([hand.combo_weight for hand in hands])

    reach_a = np.array(
        initial_reach[position_a] if position_a in initial_reach else _default_reach(),
        dtype=float,
    )
    reach_b = np.array(
        initial_reach[position_b] if position_b in initial_reach else _default_reach(),
        dtype=float,
    )

    node_data: dict = {}
    for iteration in range(iterations):
        updating_player = position_a if iteration % 2 == 0 else position_b
        _solve_recurse(
            root, reach_a.copy(), reach_b.copy(), updating_player,
            node_data, equity_table, position_a, position_b,
        )
    return node_data


# ---------------------------------------------------------------------------
# External-Sampling MCCFR (traverser-vectorized), for N-player trees.
# ---------------------------------------------------------------------------


def _mccfr_terminal_value(
    node: TerminalNode,
    traverser: str,
    opponent_hands: dict,
    num_hands: int,
    equity_cache: MultiwayEquityCache,
) -> np.ndarray:
    """The traverser's net payoff for each of their `num_hands` possible
    hands (length-`num_hands` vector), given the fixed `opponent_hands`
    sampled for this iteration."""
    if traverser in node.folded:
        return np.full(num_hands, -node.invested[traverser])

    live = [p for p in node.invested if p not in node.folded]
    other_live = [p for p in live if p != traverser]
    if not other_live:
        # Everyone else folded — the traverser wins the pot outright,
        # regardless of which hand they hold.
        return np.full(num_hands, node.pot - node.invested[traverser])

    opponent_live_hands = tuple(opponent_hands[p] for p in other_live)
    equity_vector = equity_cache.traverser_equity_vector(opponent_live_hands)
    return equity_vector * node.pot - node.invested[traverser]


def _mccfr_recurse(
    node,
    traverser: str,
    opponent_hands: dict,
    reach: np.ndarray,
    node_data: dict,
    num_hands: int,
    hand_index: dict,
    equity_cache: MultiwayEquityCache,
    rng,
) -> np.ndarray:
    """Returns the traverser's payoff vector (length num_hands) from this
    node onward, given the fixed `opponent_hands` for this iteration."""
    if isinstance(node, TerminalNode):
        return _mccfr_terminal_value(node, traverser, opponent_hands, num_hands, equity_cache)

    actions = node.legal_actions
    table = node_data.setdefault(id(node), InfoSetTable.zeros(num_hands, len(actions)))
    strategy = table.current_strategy()

    if node.player_to_act == traverser:
        # Own decision: explore every action exhaustively, vectorized
        # over all of the traverser's possible hands.
        child_values = [
            _mccfr_recurse(
                node.children[action],
                traverser,
                opponent_hands,
                reach * strategy[:, a_idx],
                node_data,
                num_hands,
                hand_index,
                equity_cache,
                rng,
            )
            for a_idx, action in enumerate(actions)
        ]
        cf_action_values = np.stack(child_values, axis=1)  # (num_hands, num_actions)
        node_value = np.einsum("ha,ha->h", strategy, cf_action_values)

        # No opponent-reach contraction needed here (unlike the exact
        # path) — opponent_hands is a single fixed sample, not a
        # distribution to marginalize over; that marginalization happens
        # implicitly across many iterations instead, via the natural
        # sampling frequency (combo_weight-proportional).
        regret = cf_action_values - node_value[:, None]
        table.regret_sum = np.maximum(table.regret_sum + regret, 0.0)  # CFR+: floor at 0
        table.strategy_sum += reach[:, None] * strategy
        return node_value

    # Opponent's decision: sample one action from their current strategy,
    # blended with a small exploration floor so no action's sampling
    # probability is ever *exactly* zero. This matters more than it might
    # look: CFR+'s regret flooring can legitimately push current_strategy()
    # to an exact 0/1 split even when the true equilibrium wants a small
    # residual frequency (e.g. "call 0.2% of the time"). Sampling from
    # that degenerate strategy directly makes the rare-but-consequential
    # branch permanently unreachable, which silently starves the
    # traverser's value estimate of it.
    #
    # We deliberately do NOT apply an importance-sampling correction here
    # (true_prob/sampling_prob) — an earlier version did, and it's the
    # textbook-unbiased choice, but it compounds *multiplicatively* across
    # every nested opponent decision on the path to a terminal (e.g. SB's
    # node then BB's node at 3-max), which produced wild-variance value
    # estimates (empirically, single-iteration outliers of -70+ on a pot
    # where the real range is roughly ±100 but should be tightly clustered
    # given typical opponent strategies). CFR+'s regret flooring makes
    # this actively destructive, not just noisy: one such outlier can wipe
    # out many iterations' worth of accumulated positive regret in a
    # single step (negative regret is floored to 0, discarding it, so
    # recovery has to rebuild from scratch) — the exact "gets worse with
    # more iterations" signature diagnosed during M8 (see the PR/commit).
    # Sampling directly from the floored strategy with weight 1 instead
    # treats that floored strategy as the opponent's actual policy for
    # this iteration, trading textbook unbiasedness for a small, bounded,
    # non-compounding bias (proportional to EXPLORATION_EPSILON, not to
    # tree depth) — verified empirically to fix convergence at N=3 without
    # regressing the N=2 exact-solver cross-validation.
    opponent_hand_idx = hand_index[opponent_hands[node.player_to_act]]
    true_probs = strategy[opponent_hand_idx]
    num_actions = len(actions)
    sampling_probs = (1.0 - EXPLORATION_EPSILON) * true_probs + EXPLORATION_EPSILON / num_actions
    sampled_idx = rng.choices(range(num_actions), weights=sampling_probs.tolist())[0]
    sampled_action = actions[sampled_idx]

    return _mccfr_recurse(
        node.children[sampled_action],
        traverser,
        opponent_hands,
        reach,
        node_data,
        num_hands,
        hand_index,
        equity_cache,
        rng,
    )


def mccfr_solve(
    root: DecisionNode,
    hands: list,
    positions: tuple,
    equity_cache: MultiwayEquityCache,
    iterations: int,
    seed: int = 0,
) -> dict:
    """Run `iterations` of External-Sampling MCCFR over `root`.

    `positions` is the acting order (matches GameConfig.positions); the
    traverser cycles through it, one per iteration. `equity_cache` should
    be constructed with the same `hands` list (see
    equity.MultiwayEquityCache) so indices line up.

    Returns a dict of {id(DecisionNode): InfoSetTable} — the same shape
    `solve()` returns, so downstream code (StrategyResult etc.) doesn't
    need to know which path produced it.

    Unlike `solve()`, this is only deterministic given a fixed `seed`
    ("same seed -> same result"), not unconditionally — opponent hands
    and actions are genuinely sampled.
    """
    num_hands = len(hands)
    hand_index = {hand: i for i, hand in enumerate(hands)}
    combo_weights = [hand.combo_weight for hand in hands]
    rng = random.Random(seed)

    node_data: dict = {}
    for iteration in range(iterations):
        traverser = positions[iteration % len(positions)]
        opponent_hands = {
            position: rng.choices(hands, weights=combo_weights)[0]
            for position in positions
            if position != traverser
        }
        reach = np.array(combo_weights)
        _mccfr_recurse(
            root, traverser, opponent_hands, reach, node_data, num_hands, hand_index, equity_cache, rng
        )
    return node_data
