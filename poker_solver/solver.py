"""Public entrypoint: solve a heads-up or multiway preflop spot end to end.

Wires together game_tree (the betting tree), equity (showdown values),
and cfr (the solving loop) behind one function, `solve_preflop`, and
formats the result into simple hand -> action -> frequency dictionaries.

Dispatches internally between cfr.py's two solving paths based on player
count: the exact, exhaustive `solve()` for heads-up (kept as a fast path,
unchanged since M4), and `mccfr_solve()` for 3+ players (exhaustive CFR
doesn't scale past 2 players — see cfr.py's module docstring and the
project plan). Both return the same {id(node): InfoSetTable} shape, so
StrategyResult doesn't need to know which path produced it.
"""

import time
from dataclasses import dataclass

from .cfr import mccfr_solve, solve
from .equity import MultiwayEquityCache, get_equity_table
from .game_tree import CALL_OR_CHECK, DecisionNode, GameConfig, build_game_tree
from .starting_hands import all_starting_hands

# Exact path (heads-up): measured ~3-4ms/iteration at full 169-hand
# scale (M4) — 1000 iterations lands comfortably in low-single-digit
# seconds.
DEFAULT_ITERATIONS = 1000

# MCCFR path (3+ players): needs far more iterations than the exact
# path to control sampling variance — see the M8 benchmarks/PR for the
# concrete numbers this was tuned against.
DEFAULT_MCCFR_ITERATIONS = 50_000


@dataclass
class StrategyResult:
    """The outcome of solving one GameConfig."""

    config: GameConfig
    root: DecisionNode
    hands: list
    node_data: dict
    iterations: int
    elapsed_seconds: float

    def strategy_at(self, node: DecisionNode) -> dict:
        """hand label -> {action label -> frequency} at any node in the
        tree this result was solved for."""
        table = self.node_data[id(node)]
        avg = table.average_strategy()
        actions = node.legal_actions
        return {
            str(hand): {str(action): float(avg[hand_idx, a_idx]) for a_idx, action in enumerate(actions)}
            for hand_idx, hand in enumerate(self.hands)
        }

    def opening_range(self) -> dict:
        """The first-to-act position's strategy at their very first
        decision (BTN's RFI spot, for the standard heads-up/positions
        convention)."""
        return self.strategy_at(self.root)

    def node_for_position(self, position: str) -> DecisionNode:
        """The decision node for `position`'s first turn to act, reached
        by everyone before them calling/checking (i.e. "the action folds
        around to you with no raise yet") — the natural generalization of
        `opening_range()`'s "BTN's very first decision" to a position that
        isn't first to act. Every position gets exactly one such node,
        found by walking call_or_check from the root: the first player to
        act is the root itself, and each subsequent position's turn
        arrives right after everyone before them has called/checked.
        """
        if position not in self.config.positions:
            raise ValueError(f"{position!r} is not one of {self.config.positions}")
        node = self.root
        while node.player_to_act != position:
            call_action = next(a for a in node.legal_actions if a.kind == CALL_OR_CHECK)
            node = node.children[call_action]
        return node

    def strategy_for_position(self, position: str) -> dict:
        """`position`'s strategy (hand -> action -> frequency) the first
        time it's their turn, assuming no raise has happened yet — see
        node_for_position. For the first-to-act position this is exactly
        opening_range()."""
        return self.strategy_at(self.node_for_position(position))


def solve_preflop(
    stack_bb: float = 100.0,
    iterations: int = None,
    config: GameConfig = None,
    hands: list = None,
    equity_table=None,
    equity_cache: MultiwayEquityCache = None,
    seed: int = 0,
) -> StrategyResult:
    """Solve a preflop spot and return its strategy.

    Either pass `stack_bb` for the default blind/sizing/raise-cap
    settings (heads-up), or pass a fully custom `config` directly (e.g.
    with `positions=("BTN", "SB", "BB")` for 3-max).

    `hands` defaults to the full 169-class canonical set — override for
    smaller/faster test solves. `equity_table` (heads-up only) and
    `equity_cache` (3+ players only) default to the real disk-cached
    table / a fresh lazy cache respectively — override together with
    `hands` for fast, self-contained testing.

    `iterations` defaults to DEFAULT_ITERATIONS for heads-up or
    DEFAULT_MCCFR_ITERATIONS for 3+ players — the two paths need very
    different scales, so leave it unset unless you have a specific
    reason to override.
    """
    config = config if config is not None else GameConfig(stack_bb=stack_bb)
    root = build_game_tree(config)
    hands = hands if hands is not None else all_starting_hands()

    start = time.perf_counter()
    if config.num_players == 2:
        actual_iterations = iterations if iterations is not None else DEFAULT_ITERATIONS
        equity_table = equity_table if equity_table is not None else get_equity_table()
        node_data = solve(root, hands, equity_table, iterations=actual_iterations)
    else:
        actual_iterations = iterations if iterations is not None else DEFAULT_MCCFR_ITERATIONS
        equity_cache = equity_cache if equity_cache is not None else MultiwayEquityCache(hands=hands)
        node_data = mccfr_solve(
            root, hands, config.positions, equity_cache, iterations=actual_iterations, seed=seed
        )
    elapsed = time.perf_counter() - start

    return StrategyResult(
        config=config,
        root=root,
        hands=hands,
        node_data=node_data,
        iterations=actual_iterations,
        elapsed_seconds=elapsed,
    )


def format_opening_range_grid(result: StrategyResult) -> str:
    """A quick 13x13-ish text dump of the opening range, sorted by hand
    strength, for eyeballing sanity (used by the console script)."""
    opening = result.opening_range()
    non_fold_action_by_strength = []
    for hand in result.hands:
        freqs = opening[str(hand)]
        fold_freq = freqs.get("fold", 0.0)
        non_fold_action_by_strength.append((str(hand), 1.0 - fold_freq, freqs))

    lines = [
        f"{result.root.player_to_act} opening range @ {result.config.stack_bb:.0f}bb "
        f"({result.iterations} iterations, {result.elapsed_seconds:.2f}s):",
    ]
    for label, non_fold_freq, freqs in non_fold_action_by_strength:
        breakdown = ", ".join(f"{action}={freq:.2f}" for action, freq in freqs.items())
        lines.append(f"  {label:>4s}  non-fold={non_fold_freq:5.1%}   ({breakdown})")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    stack = float(sys.argv[1]) if len(sys.argv) > 1 else 100.0
    iters = int(sys.argv[2]) if len(sys.argv) > 2 else None
    result = solve_preflop(stack_bb=stack, iterations=iters)
    print(format_opening_range_grid(result))
