"""Tests for poker_solver's top-level public API (poker_solver/__init__.py)."""

import poker_solver


def test_solve_preflop_is_exported_and_callable():
    config = poker_solver.GameConfig(raise_sizes=(), max_raises=1)
    result = poker_solver.solve_preflop(iterations=10, config=config)
    assert isinstance(result, poker_solver.StrategyResult)


def test_all_starting_hands_exported():
    hands = poker_solver.all_starting_hands()
    assert len(hands) == 169
    assert all(isinstance(hand, poker_solver.StartingHand) for hand in hands)


def test_build_game_tree_exported():
    config = poker_solver.GameConfig(raise_sizes=(), max_raises=1)
    root = poker_solver.build_game_tree(config)
    assert root.player_to_act == "BTN"


def test_public_api_surface_matches_all():
    exported = {name for name in dir(poker_solver) if not name.startswith("_")}
    assert set(poker_solver.__all__) <= exported
