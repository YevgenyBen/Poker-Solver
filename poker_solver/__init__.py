"""Heads-up preflop GTO solver — usable standalone, no web framework required.

    import poker_solver
    result = poker_solver.solve_preflop(stack_bb=100)
    print(result.opening_range())

This package has no dependency on the FastAPI app in api/ (see
tests/test_package_boundary.py, which enforces that) — api/ depends on
this package, never the reverse.
"""

from .game_tree import GameConfig, build_game_tree
from .solver import StrategyResult, solve_preflop
from .starting_hands import StartingHand, all_starting_hands

__all__ = [
    "GameConfig",
    "StartingHand",
    "StrategyResult",
    "all_starting_hands",
    "build_game_tree",
    "solve_preflop",
]
