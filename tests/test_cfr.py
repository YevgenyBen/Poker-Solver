import numpy as np
import pytest

from poker_solver.cfr import InfoSetTable, solve
from poker_solver.equity import build_equity_table
from poker_solver.game_tree import ALL_IN, BTN, FOLD, RAISE, Action, DecisionNode, GameConfig, TerminalNode, build_game_tree
from poker_solver.starting_hands import StartingHand


# ---------------------------------------------------------------------------
# InfoSetTable
# ---------------------------------------------------------------------------


def test_infoset_table_zeros_shape():
    table = InfoSetTable.zeros(num_hands=3, num_actions=2)
    assert table.regret_sum.shape == (3, 2)
    assert table.strategy_sum.shape == (3, 2)


def test_current_strategy_is_uniform_with_zero_regret():
    table = InfoSetTable.zeros(num_hands=2, num_actions=2)
    strategy = table.current_strategy()
    assert np.allclose(strategy, 0.5)


def test_current_strategy_follows_positive_regret():
    table = InfoSetTable.zeros(num_hands=1, num_actions=2)
    table.regret_sum = np.array([[3.0, 1.0]])
    strategy = table.current_strategy()
    assert np.allclose(strategy, [[0.75, 0.25]])


def test_average_strategy_uniform_when_never_accumulated():
    table = InfoSetTable.zeros(num_hands=1, num_actions=3)
    avg = table.average_strategy()
    assert np.allclose(avg, [[1 / 3, 1 / 3, 1 / 3]])


def test_average_strategy_normalizes_accumulated_sum():
    table = InfoSetTable.zeros(num_hands=1, num_actions=2)
    table.strategy_sum = np.array([[3.0, 1.0]])
    avg = table.average_strategy()
    assert np.allclose(avg, [[0.75, 0.25]])


# ---------------------------------------------------------------------------
# Toy games with a known, hand-verifiable equilibrium.
# ---------------------------------------------------------------------------


def _toy_game(showdown_pot, showdown_btn_invested, equity_value):
    fold_terminal = TerminalNode(pot=1.5, btn_invested=0.5, bb_invested=1.0, folded_player=BTN)
    showdown_terminal = TerminalNode(
        pot=showdown_pot, btn_invested=showdown_btn_invested, bb_invested=showdown_btn_invested, folded_player=None
    )
    root = DecisionNode(
        player_to_act=BTN,
        pot=1.5,
        btn_invested=0.5,
        bb_invested=1.0,
        raises_so_far=0,
        children={Action(FOLD): fold_terminal, Action(RAISE, showdown_btn_invested): showdown_terminal},
    )
    hands = [StartingHand("A", "A"), StartingHand("7", "2", suited=False)]
    equity_table = np.full((2, 2), equity_value)
    return root, hands, equity_table


def test_cfr_converges_to_dominant_raise():
    # Raise nets 0.9*10 - 1 = 8.0, fold nets -0.5: raising strictly
    # dominates for both hands, regardless of what's actually held.
    root, hands, equity_table = _toy_game(showdown_pot=10.0, showdown_btn_invested=1.0, equity_value=0.9)
    node_data = solve(root, hands, equity_table, iterations=200)
    avg = node_data[id(root)].average_strategy()
    raise_idx = root.legal_actions.index(Action(RAISE, 1.0))
    assert np.all(avg[:, raise_idx] > 0.95)


def test_cfr_converges_to_dominant_fold():
    # Raise nets 0.05*10 - 2 = -1.5, fold nets -0.5: folding strictly
    # dominates for both hands.
    root, hands, equity_table = _toy_game(showdown_pot=10.0, showdown_btn_invested=2.0, equity_value=0.05)
    node_data = solve(root, hands, equity_table, iterations=200)
    avg = node_data[id(root)].average_strategy()
    fold_idx = root.legal_actions.index(Action(FOLD))
    assert np.all(avg[:, fold_idx] > 0.95)


def test_solve_is_exactly_deterministic():
    root, hands, equity_table = _toy_game(showdown_pot=10.0, showdown_btn_invested=1.0, equity_value=0.6)
    result1 = solve(root, hands, equity_table, iterations=50)
    result2 = solve(root, hands, equity_table, iterations=50)
    assert np.array_equal(result1[id(root)].strategy_sum, result2[id(root)].strategy_sum)
    assert np.array_equal(result1[id(root)].regret_sum, result2[id(root)].regret_sum)


def test_average_strategy_rows_sum_to_one_and_have_no_nans():
    root, hands, equity_table = _toy_game(showdown_pot=10.0, showdown_btn_invested=1.0, equity_value=0.6)
    node_data = solve(root, hands, equity_table, iterations=50)
    for table in node_data.values():
        avg = table.average_strategy()
        assert not np.any(np.isnan(avg))
        assert np.allclose(avg.sum(axis=1), 1.0)


def test_equity_table_shape_mismatch_raises():
    root, hands, _ = _toy_game(showdown_pot=10.0, showdown_btn_invested=1.0, equity_value=0.6)
    wrong_shape_table = np.full((3, 3), 0.5)
    with pytest.raises(ValueError):
        solve(root, hands, wrong_shape_table, iterations=1)


# ---------------------------------------------------------------------------
# A real (tiny) tree using the actual equity table: directional sanity.
# ---------------------------------------------------------------------------


def test_real_tiny_tree_aa_plays_much_more_aggressively_than_72o():
    config = GameConfig(raise_sizes=(), max_raises=1)  # small tree: fold/call/all-in only
    root = build_game_tree(config)
    hands = [StartingHand("A", "A"), StartingHand("7", "2", suited=False)]
    equity_table = build_equity_table(hands=hands, samples=300)

    node_data = solve(root, hands, equity_table, iterations=500)
    avg = node_data[id(root)].average_strategy()

    allin_idx = root.legal_actions.index(Action(ALL_IN, config.stack_bb))
    fold_idx = root.legal_actions.index(Action(FOLD))

    aa_allin, weak_allin = avg[0, allin_idx], avg[1, allin_idx]
    aa_fold, weak_fold = avg[0, fold_idx], avg[1, fold_idx]

    assert aa_allin > weak_allin
    assert weak_fold >= aa_fold
