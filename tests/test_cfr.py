import numpy as np
import pytest

from poker_solver.cfr import InfoSetTable, mccfr_solve, solve
from poker_solver.equity import MultiwayEquityCache, build_equity_table
from poker_solver.game_tree import ALL_IN, BB, BTN, CALL_OR_CHECK, FOLD, RAISE, Action, DecisionNode, GameConfig, TerminalNode, build_game_tree
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
    fold_terminal = TerminalNode(pot=1.5, invested={BTN: 0.5, BB: 1.0}, folded=frozenset({BTN}))
    showdown_terminal = TerminalNode(
        pot=showdown_pot,
        invested={BTN: showdown_btn_invested, BB: showdown_btn_invested},
        folded=frozenset(),
    )
    root = DecisionNode(
        player_to_act=BTN,
        pot=1.5,
        invested={BTN: 0.5, BB: 1.0},
        folded=frozenset(),
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
# M11: solve()'s two generalizations — custom position labels (for a
# postflop tree, whose player_to_act values aren't BTN/BB) and custom
# initial_reach (a real range from earlier action, not combo_weight).
# Both must default to exactly today's preflop behavior when omitted.
# ---------------------------------------------------------------------------


def test_solve_accepts_custom_position_labels():
    # Same math/shape as test_cfr_converges_to_dominant_raise, just with
    # postflop-style labels instead of BTN/BB — proves solve() doesn't
    # secretly depend on the module's BTN/BB constants anywhere.
    fold_terminal = TerminalNode(pot=1.5, invested={"OOP": 0.5, "IP": 1.0}, folded=frozenset({"OOP"}))
    showdown_terminal = TerminalNode(pot=10.0, invested={"OOP": 1.0, "IP": 1.0}, folded=frozenset())
    root = DecisionNode(
        player_to_act="OOP",
        pot=1.5,
        invested={"OOP": 0.5, "IP": 1.0},
        folded=frozenset(),
        raises_so_far=0,
        children={Action(FOLD): fold_terminal, Action(RAISE, 1.0): showdown_terminal},
    )
    hands = [StartingHand("A", "A"), StartingHand("7", "2", suited=False)]
    equity_table = np.full((2, 2), 0.9)  # raise nets 0.9*10-1=8.0 vs fold's -0.5 — strictly dominates
    node_data = solve(root, hands, equity_table, iterations=200, positions=("OOP", "IP"))
    avg = node_data[id(root)].average_strategy()
    raise_idx = root.legal_actions.index(Action(RAISE, 1.0))
    assert np.all(avg[:, raise_idx] > 0.95)


def test_solve_initial_reach_overrides_default_combo_weight():
    root, hands, equity_table = _toy_game(showdown_pot=10.0, showdown_btn_invested=1.0, equity_value=0.6)
    zero_then_one = np.array([0.0, 1.0])  # AA (index 0) gets zero reach as BTN
    node_data = solve(root, hands, equity_table, iterations=50, initial_reach={BTN: zero_then_one})
    # AA never contributes to strategy_sum accumulation with zero reach —
    # a directly verifiable consequence of the override actually taking
    # effect, not just "it ran without crashing."
    assert np.all(node_data[id(root)].strategy_sum[0, :] == 0.0)


def test_solve_initial_reach_none_matches_omitting_it_entirely():
    root, hands, equity_table = _toy_game(showdown_pot=10.0, showdown_btn_invested=1.0, equity_value=0.6)
    omitted = solve(root, hands, equity_table, iterations=50)
    explicit_none = solve(root, hands, equity_table, iterations=50, initial_reach=None)
    assert np.array_equal(omitted[id(root)].strategy_sum, explicit_none[id(root)].strategy_sum)
    assert np.array_equal(omitted[id(root)].regret_sum, explicit_none[id(root)].regret_sum)


def test_solve_initial_reach_partial_override_still_produces_well_formed_output():
    # Only BTN is overridden — BB should transparently fall back to its
    # default combo_weight-derived reach, not crash or produce NaNs.
    root, hands, equity_table = _toy_game(showdown_pot=10.0, showdown_btn_invested=1.0, equity_value=0.6)
    node_data = solve(root, hands, equity_table, iterations=50, initial_reach={BTN: np.array([1.0, 1.0])})
    avg = node_data[id(root)].average_strategy()
    assert not np.any(np.isnan(avg))
    assert np.allclose(avg.sum(axis=1), 1.0)


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


# ---------------------------------------------------------------------------
# mccfr_solve — toy games with a known, hand-verifiable equilibrium.
# Uses a stub equity "cache" (same value regardless of opponent hands)
# for full control, same spirit as _toy_game above but through the
# MCCFR interface (which consumes a cache object, not a raw array).
# ---------------------------------------------------------------------------


class _StubEquityCache:
    """Returns the same equity value for every traverser hand,
    regardless of opponent hands — for fully-controlled toy-game tests."""

    def __init__(self, equity_value: float, num_hands: int):
        self._value = equity_value
        self._num_hands = num_hands

    def traverser_equity_vector(self, opponent_hands):
        return np.full(self._num_hands, self._value)


def _mccfr_toy_game(showdown_pot, showdown_btn_invested, equity_value):
    fold_terminal = TerminalNode(pot=1.5, invested={BTN: 0.5, BB: 1.0}, folded=frozenset({BTN}))
    showdown_terminal = TerminalNode(
        pot=showdown_pot,
        invested={BTN: showdown_btn_invested, BB: showdown_btn_invested},
        folded=frozenset(),
    )
    root = DecisionNode(
        player_to_act=BTN,
        pot=1.5,
        invested={BTN: 0.5, BB: 1.0},
        folded=frozenset(),
        raises_so_far=0,
        children={Action(FOLD): fold_terminal, Action(RAISE, showdown_btn_invested): showdown_terminal},
    )
    hands = [StartingHand("A", "A"), StartingHand("7", "2", suited=False)]
    equity_cache = _StubEquityCache(equity_value, num_hands=len(hands))
    return root, hands, equity_cache


def test_mccfr_converges_to_dominant_raise():
    # Raise nets 0.9*10 - 1 = 8.0, fold nets -0.5: strictly dominant.
    # Looser tolerance than the exact path's 0.95 (per the M8 plan —
    # sampling introduces real variance around the true equilibrium).
    root, hands, equity_cache = _mccfr_toy_game(showdown_pot=10.0, showdown_btn_invested=1.0, equity_value=0.9)
    node_data = mccfr_solve(root, hands, positions=(BTN, BB), equity_cache=equity_cache, iterations=2000, seed=1)
    avg = node_data[id(root)].average_strategy()
    raise_idx = root.legal_actions.index(Action(RAISE, 1.0))
    assert np.all(avg[:, raise_idx] > 0.85)


def test_mccfr_converges_to_dominant_fold():
    # Raise nets 0.05*10 - 2 = -1.5, fold nets -0.5: strictly dominant.
    root, hands, equity_cache = _mccfr_toy_game(showdown_pot=10.0, showdown_btn_invested=2.0, equity_value=0.05)
    node_data = mccfr_solve(root, hands, positions=(BTN, BB), equity_cache=equity_cache, iterations=2000, seed=1)
    avg = node_data[id(root)].average_strategy()
    fold_idx = root.legal_actions.index(Action(FOLD))
    assert np.all(avg[:, fold_idx] > 0.85)


def test_mccfr_solve_is_deterministic_given_a_seed():
    root, hands, equity_cache = _mccfr_toy_game(10.0, 1.0, 0.6)
    first = mccfr_solve(root, hands, positions=(BTN, BB), equity_cache=equity_cache, iterations=200, seed=7)
    second = mccfr_solve(root, hands, positions=(BTN, BB), equity_cache=equity_cache, iterations=200, seed=7)
    assert np.array_equal(first[id(root)].strategy_sum, second[id(root)].strategy_sum)
    assert np.array_equal(first[id(root)].regret_sum, second[id(root)].regret_sum)


def test_mccfr_average_strategy_rows_sum_to_one_and_have_no_nans():
    root, hands, equity_cache = _mccfr_toy_game(10.0, 1.0, 0.6)
    node_data = mccfr_solve(root, hands, positions=(BTN, BB), equity_cache=equity_cache, iterations=300, seed=3)
    for table in node_data.values():
        avg = table.average_strategy()
        assert not np.any(np.isnan(avg))
        assert np.allclose(avg.sum(axis=1), 1.0)


def test_mccfr_works_with_three_players_on_the_toy_tree_shape():
    # A minimal check that mccfr_solve doesn't choke on positions beyond
    # BTN/BB — full 3-max structural/directional coverage lives in the
    # real-tree tests below and in test_solver.py once solver.py wires
    # up the N-player dispatch.
    fold_terminal = TerminalNode(pot=2.5, invested={"BTN": 0.0, "SB": 0.5, "BB": 1.0}, folded=frozenset({"BTN"}))
    showdown_terminal = TerminalNode(
        pot=3.0, invested={"BTN": 1.0, "SB": 1.0, "BB": 1.0}, folded=frozenset()
    )
    root = DecisionNode(
        player_to_act="BTN",
        pot=1.5,
        invested={"BTN": 0.0, "SB": 0.5, "BB": 1.0},
        folded=frozenset(),
        raises_so_far=0,
        children={Action(FOLD): fold_terminal, Action(CALL_OR_CHECK): showdown_terminal},
    )
    hands = [StartingHand("A", "A"), StartingHand("7", "2", suited=False)]
    equity_cache = _StubEquityCache(0.4, num_hands=len(hands))
    node_data = mccfr_solve(
        root, hands, positions=("BTN", "SB", "BB"), equity_cache=equity_cache, iterations=100, seed=1
    )
    avg = node_data[id(root)].average_strategy()
    assert np.allclose(avg.sum(axis=1), 1.0)


# ---------------------------------------------------------------------------
# Cross-validation: at N=2, MCCFR and the exact solver should agree,
# since they're solving the exact same game — this is the strongest
# correctness signal for MCCFR (validated against a trusted baseline,
# not just toy known-answers).
#
# HISTORY: an earlier version of this test used an importance-sampling
# correction (true_prob/sampling_prob) on opponent-action sampling, which
# is the textbook-unbiased choice and converged fast enough here (~80k
# iterations) to look correct. It was reverted during M8 once real
# (non-toy) 3-max solves revealed the correction compounds
# *multiplicatively* across nested opponent decisions (e.g. SB's node
# then BB's node), producing high-variance value estimates that CFR+'s
# regret flooring turns actively destructive — see EXPLORATION_EPSILON
# and _mccfr_recurse in cfr.py for the full writeup. Sampling directly
# from the floored strategy (no correction) fixed 3-max convergence but
# trades a small, bounded, epsilon-proportional bias for the removed
# variance — which is why this N=2 toy game (a single opponent decision,
# so no compounding either way) now needs *more* iterations than before
# to converge to the same precision: the correction's unbiasedness isn't
# free, it was just cheap here specifically because there's only one
# opponent level to bias. 300k iterations (still well under a second here)
# reliably lands within tolerance for the pinned seed.
# ---------------------------------------------------------------------------


def test_mccfr_agrees_with_exact_solve_at_heads_up():
    config = GameConfig(raise_sizes=(), max_raises=1)
    root = build_game_tree(config)
    hands = [StartingHand("A", "A"), StartingHand("7", "2", suited=False)]

    exact_equity_table = build_equity_table(hands=hands, samples=300)
    exact_avg = solve(root, hands, exact_equity_table, iterations=500)[id(root)].average_strategy()

    mccfr_equity_cache = MultiwayEquityCache(hands=hands, samples=200, seed=1)
    mccfr_avg = mccfr_solve(
        root, hands, positions=(BTN, BB), equity_cache=mccfr_equity_cache, iterations=300_000, seed=1
    )[id(root)].average_strategy()

    allin_idx = root.legal_actions.index(Action(ALL_IN, config.stack_bb))
    fold_idx = root.legal_actions.index(Action(FOLD))
    # AA's all-in frequency and 72o's fold frequency should roughly
    # agree between the two solvers — both are solving the same game.
    assert abs(exact_avg[0, allin_idx] - mccfr_avg[0, allin_idx]) < 0.2
    assert abs(exact_avg[1, fold_idx] - mccfr_avg[1, fold_idx]) < 0.2
