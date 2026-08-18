"""Public entrypoint: solve a heads-up preflop spot end to end.

Wires together game_tree (the betting tree), equity (showdown values),
and cfr (the solving loop) behind one function, `solve_preflop`, and
formats the result into simple hand -> action -> frequency dictionaries.
"""

import time
from dataclasses import dataclass

from .cfr import solve
from .equity import get_equity_table
from .game_tree import DecisionNode, GameConfig, build_game_tree
from .starting_hands import all_starting_hands

# Measured on a laptop CPU: ~3-4ms/iteration at full 169-hand scale
# (vectorized CFR+ over the whole tree per iteration, not per hand pair)
# — 1000 iterations lands comfortably in low-single-digit seconds. The
# real bottleneck is the *first-ever* equity table build on a machine
# (minutes, one-time, then disk-cached — see equity.py), not solving.
DEFAULT_ITERATIONS = 1000


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
        """BTN's strategy at the very first decision (the RFI spot)."""
        return self.strategy_at(self.root)


def solve_preflop(
    stack_bb: float = 100.0,
    iterations: int = DEFAULT_ITERATIONS,
    config: GameConfig = None,
    hands: list = None,
    equity_table=None,
) -> StrategyResult:
    """Solve a heads-up preflop spot and return its strategy.

    Either pass `stack_bb` for the default blind/sizing/raise-cap
    settings, or pass a fully custom `config` directly.

    `hands`/`equity_table` default to the full 169-class canonical set
    and the real (disk-cached) preflop equity table — override both
    together with a smaller hand set for fast, self-contained testing
    (avoids depending on the full table, which is a slow one-time build
    the first time it's ever needed on a machine).
    """
    config = config if config is not None else GameConfig(stack_bb=stack_bb)
    root = build_game_tree(config)
    hands = hands if hands is not None else all_starting_hands()
    equity_table = equity_table if equity_table is not None else get_equity_table()

    start = time.perf_counter()
    node_data = solve(root, hands, equity_table, iterations=iterations)
    elapsed = time.perf_counter() - start

    return StrategyResult(
        config=config,
        root=root,
        hands=hands,
        node_data=node_data,
        iterations=iterations,
        elapsed_seconds=elapsed,
    )


def format_opening_range_grid(result: StrategyResult) -> str:
    """A quick 13x13-ish text dump of BTN's opening range, sorted by
    hand strength, for eyeballing sanity (used by the console script)."""
    opening = result.opening_range()
    non_fold_action_by_strength = []
    for hand in result.hands:
        freqs = opening[str(hand)]
        fold_freq = freqs.get("fold", 0.0)
        non_fold_action_by_strength.append((str(hand), 1.0 - fold_freq, freqs))

    lines = [
        f"BTN opening range @ {result.config.stack_bb:.0f}bb "
        f"({result.iterations} iterations, {result.elapsed_seconds:.2f}s):",
    ]
    for label, non_fold_freq, freqs in non_fold_action_by_strength:
        breakdown = ", ".join(f"{action}={freq:.2f}" for action, freq in freqs.items())
        lines.append(f"  {label:>4s}  non-fold={non_fold_freq:5.1%}   ({breakdown})")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    stack = float(sys.argv[1]) if len(sys.argv) > 1 else 100.0
    iters = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_ITERATIONS
    result = solve_preflop(stack_bb=stack, iterations=iters)
    print(format_opening_range_grid(result))
