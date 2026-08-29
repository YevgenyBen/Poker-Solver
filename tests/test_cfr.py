import random

import numpy as np
import pytest

from poker_solver import cfr
from poker_solver.cards import Card
from poker_solver.cfr import (
    InfoSetTable,
    _mccfr_recurse,
    _mccfr_terminal_value,
    _opponent_hands_are_dealable,
    _sample_chance_card,
    _sample_opponent_hands,
    mccfr_solve,
    solve,
)
from poker_solver.chance import ChanceBranch, ChanceNode, SampledChanceBranch, build_mccfr_chance_branch
from poker_solver.combos import HandCombo
from poker_solver.equity import MultiwayEquityCache, build_equity_table
from poker_solver.game_tree import (
    ALL_IN,
    BB,
    BTN,
    CALL_OR_CHECK,
    FOLD,
    RAISE,
    Action,
    DecisionNode,
    GameConfig,
    StreetConfig,
    TerminalNode,
    build_game_tree,
    build_street_tree,
)
from poker_solver.multiway_board_equity import NwayBoardEquityCache
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


def test_trained_mask_all_false_when_never_accumulated():
    table = InfoSetTable.zeros(num_hands=3, num_actions=2)
    assert list(table.trained_mask()) == [False, False, False]


def test_trained_mask_true_only_for_hands_with_accumulated_strategy():
    table = InfoSetTable.zeros(num_hands=3, num_actions=2)
    table.strategy_sum = np.array([[3.0, 1.0], [0.0, 0.0], [0.0, 0.5]])
    assert list(table.trained_mask()) == [True, False, True]


def test_trained_mask_matches_average_strategy_fallback_exactly():
    # trained_mask() must agree, row for row, with which rows
    # average_strategy() actually falls back to uniform for — it's the
    # same underlying condition, just exposed instead of discarded. Uses
    # an *asymmetric* accumulated row deliberately — a symmetric one
    # (e.g. [2.0, 2.0]) would normalize to the same [0.5, 0.5] the
    # uniform fallback also produces, which would make this assertion
    # pass by numeric coincidence rather than actually distinguishing
    # "real, trained data" from "never accumulated at all" — precisely
    # the ambiguity trained_mask exists to resolve.
    table = InfoSetTable.zeros(num_hands=2, num_actions=2)
    table.strategy_sum = np.array([[3.0, 1.0], [0.0, 0.0]])
    mask = table.trained_mask()
    avg = table.average_strategy()
    assert mask[0] and np.allclose(avg[0], [0.75, 0.25])  # real, asymmetric, trained data
    assert not mask[1] and np.allclose(avg[1], [0.5, 0.5])  # untrained row: the uniform fallback


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
    # 5,000 iterations, not 500. The exact solver is unsampled but its
    # time-average still needs to converge, and at 500 it had not: AA's
    # averaged all-in frequency read 0.656 there and drifts to 0.892 /
    # 0.956 / 0.969 at 2k / 10k / 50k (M71 measured this directly). A
    # cross-validation is only meaningful once BOTH arms are converged —
    # otherwise it measures the reference's own error, and a correct
    # change to the sampled solver shows up as a regression. That is
    # exactly what happened when M71 dropped the CFR+ regret clamp.
    exact_avg = solve(root, hands, exact_equity_table, iterations=5_000)[id(root)].average_strategy()

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


# ---------------------------------------------------------------------------
# M12: solve()'s chance_fn/chance_data — chaining a showdown-eligible
# terminal into a ChanceNode (e.g. flop -> turn) instead of an immediate
# showdown. See chance.py's module docstring and cfr.py's own for the
# design (uniform-average-over-branches, and why chance dispatch has to
# turn itself off per-branch rather than thread through unconditionally).
# ---------------------------------------------------------------------------

_ONE_HAND = [StartingHand("A", "A")]
_DUMMY_1X1_EQUITY_TABLE = np.array([[0.5]])


def test_solve_without_chance_fn_matches_pre_m12_behavior():
    # Same toy game as test_solve_initial_reach_none_matches_omitting_it_
    # entirely, but for the new chance_fn/chance_data params: omitting
    # them entirely must be byte-identical to passing chance_fn=None,
    # chance_data=None explicitly — the whole backward-compatibility
    # story for every pre-M12 call site.
    root, hands, equity_table = _toy_game(showdown_pot=10.0, showdown_btn_invested=1.0, equity_value=0.6)
    omitted = solve(root, hands, equity_table, iterations=50)
    explicit_none = solve(root, hands, equity_table, iterations=50, chance_fn=None, chance_data=None)
    assert np.array_equal(omitted[id(root)].strategy_sum, explicit_none[id(root)].strategy_sum)
    assert np.array_equal(omitted[id(root)].regret_sum, explicit_none[id(root)].regret_sum)


def test_solve_chance_fn_not_called_for_fold_out_terminals():
    # A tree whose only terminal is a fold-out (not showdown-eligible) —
    # a chance_fn that raises if ever called proves dispatch correctly
    # never fires there.
    fold_terminal = TerminalNode(pot=1.5, invested={BTN: 0.5, BB: 1.0}, folded=frozenset({BTN}))
    root = DecisionNode(
        player_to_act=BTN, pot=1.5, invested={BTN: 0.5, BB: 1.0}, folded=frozenset(),
        raises_so_far=0, children={Action(FOLD): fold_terminal},
    )

    def _chance_fn(terminal):
        raise AssertionError("chance_fn should never be called for a fold-out terminal")

    solve(root, _ONE_HAND, _DUMMY_1X1_EQUITY_TABLE, iterations=10, chance_fn=_chance_fn)


def test_solve_averages_chance_node_branches_not_sums_them():
    # Two branches whose hand-computed values average to -0.3 (raise
    # narrowly beats fold's -0.5) but *sum* to -0.6 (fold would
    # incorrectly beat -0.5 if the implementation summed instead of
    # averaged) — a genuine arithmetic distinguisher, not just "some
    # value came out and didn't crash." branch1: 0.1*5.0-1.0=-0.5;
    # branch2: 0.3*3.0-1.0=-0.1; average=-0.3 > fold's -0.5.
    fold_terminal = TerminalNode(pot=1.5, invested={BTN: 0.5, BB: 1.0}, folded=frozenset({BTN}))
    branch_1 = ChanceBranch(
        card=Card("2", "c"), equity_table=np.array([[0.1]]),
        root=TerminalNode(pot=5.0, invested={BTN: 1.0, BB: 1.0}, folded=frozenset()),
    )
    branch_2 = ChanceBranch(
        card=Card("3", "c"), equity_table=np.array([[0.3]]),
        root=TerminalNode(pot=3.0, invested={BTN: 1.0, BB: 1.0}, folded=frozenset()),
    )
    chance_node = ChanceNode(
        pot=1.5, invested={BTN: 1.0, BB: 1.0}, branches={branch_1.card: branch_1, branch_2.card: branch_2}
    )
    root = DecisionNode(
        player_to_act=BTN, pot=1.5, invested={BTN: 0.5, BB: 1.0}, folded=frozenset(), raises_so_far=0,
        children={Action(FOLD): fold_terminal, Action(RAISE, 1.0): chance_node},
    )
    node_data = solve(root, _ONE_HAND, _DUMMY_1X1_EQUITY_TABLE, iterations=300)
    avg = node_data[id(root)].average_strategy()
    raise_idx = root.legal_actions.index(Action(RAISE, 1.0))
    assert avg[0, raise_idx] > 0.95


def test_solve_no_infoset_table_created_for_a_chance_node():
    fold_terminal = TerminalNode(pot=1.5, invested={BTN: 0.5, BB: 1.0}, folded=frozenset({BTN}))
    branch = ChanceBranch(
        card=Card("2", "c"), equity_table=np.array([[0.6]]),
        root=TerminalNode(pot=10.0, invested={BTN: 1.0, BB: 1.0}, folded=frozenset()),
    )
    chance_node = ChanceNode(pot=1.5, invested={BTN: 1.0, BB: 1.0}, branches={branch.card: branch})
    root = DecisionNode(
        player_to_act=BTN, pot=1.5, invested={BTN: 0.5, BB: 1.0}, folded=frozenset(), raises_so_far=0,
        children={Action(FOLD): fold_terminal, Action(RAISE, 1.0): chance_node},
    )
    node_data = solve(root, _ONE_HAND, _DUMMY_1X1_EQUITY_TABLE, iterations=20)
    assert id(chance_node) not in node_data


def test_solve_end_to_end_real_decision_nodes_beneath_a_chance_node():
    # The chance branch leads into a real DecisionNode (fold/call), not
    # straight to a terminal — regret/strategy should accumulate there
    # correctly too, the same as any other DecisionNode in the tree.
    fold_terminal = TerminalNode(pot=1.5, invested={BTN: 0.5, BB: 1.0}, folded=frozenset({BTN}))
    turn_fold_terminal = TerminalNode(pot=1.5, invested={BTN: 1.0, BB: 1.0}, folded=frozenset({BTN}))
    turn_showdown_terminal = TerminalNode(pot=10.0, invested={BTN: 3.0, BB: 3.0}, folded=frozenset())
    turn_decision = DecisionNode(
        player_to_act=BTN, pot=1.5, invested={BTN: 1.0, BB: 1.0}, folded=frozenset(), raises_so_far=0,
        children={Action(FOLD): turn_fold_terminal, Action(RAISE, 3.0): turn_showdown_terminal},
    )
    branch = ChanceBranch(card=Card("2", "c"), equity_table=np.array([[0.9]]), root=turn_decision)
    chance_node = ChanceNode(pot=1.5, invested={BTN: 1.0, BB: 1.0}, branches={branch.card: branch})
    root = DecisionNode(
        player_to_act=BTN, pot=1.5, invested={BTN: 0.5, BB: 1.0}, folded=frozenset(), raises_so_far=0,
        children={Action(FOLD): fold_terminal, Action(RAISE, 1.0): chance_node},
    )
    node_data = solve(root, _ONE_HAND, _DUMMY_1X1_EQUITY_TABLE, iterations=100)
    assert id(turn_decision) in node_data
    avg = node_data[id(turn_decision)].average_strategy()
    assert not np.any(np.isnan(avg))
    assert np.allclose(avg.sum(axis=1), 1.0)


def test_solve_chance_fn_memoized_across_iterations():
    showdown_terminal = TerminalNode(pot=10.0, invested={BTN: 1.0, BB: 1.0}, folded=frozenset())
    fold_terminal = TerminalNode(pot=1.5, invested={BTN: 0.5, BB: 1.0}, folded=frozenset({BTN}))
    root = DecisionNode(
        player_to_act=BTN, pot=1.5, invested={BTN: 0.5, BB: 1.0}, folded=frozenset(), raises_so_far=0,
        children={Action(FOLD): fold_terminal, Action(RAISE, 1.0): showdown_terminal},
    )
    call_count = [0]

    def _chance_fn(terminal):
        call_count[0] += 1
        branch = ChanceBranch(
            card=Card("2", "c"), equity_table=np.array([[0.5]]),
            root=TerminalNode(pot=terminal.pot, invested=dict(terminal.invested), folded=frozenset()),
        )
        return ChanceNode(pot=terminal.pot, invested=dict(terminal.invested), branches={branch.card: branch})

    solve(root, _ONE_HAND, _DUMMY_1X1_EQUITY_TABLE, iterations=50, chance_fn=_chance_fn)
    assert call_count[0] == 1


def test_solve_does_not_recurse_chance_fn_into_branch_subtrees():
    # Regression guard for the scoping bug the design explicitly calls
    # out: a branch's own subtree reaching its own showdown terminal must
    # NOT re-trigger the *outer* (flop-scoped) chance_fn — that would
    # double-deal a card off the wrong board. The chance_fn spy here
    # returns a branch whose root is itself a showdown terminal
    # (chance_fn=None, the M12 default) — if dispatch were threaded
    # unconditionally instead of turned off per-branch, call_count would
    # be 2 (or more, across iterations), not 1.
    showdown_terminal = TerminalNode(pot=10.0, invested={BTN: 1.0, BB: 1.0}, folded=frozenset())
    fold_terminal = TerminalNode(pot=1.5, invested={BTN: 0.5, BB: 1.0}, folded=frozenset({BTN}))
    root = DecisionNode(
        player_to_act=BTN, pot=1.5, invested={BTN: 0.5, BB: 1.0}, folded=frozenset(), raises_so_far=0,
        children={Action(FOLD): fold_terminal, Action(RAISE, 1.0): showdown_terminal},
    )
    call_count = [0]

    def _chance_fn(terminal):
        call_count[0] += 1
        # The branch's own root is itself a showdown-eligible terminal —
        # correct behavior treats it as a real (river-averaged) showdown,
        # not something to deal yet another card for.
        branch_showdown = TerminalNode(pot=terminal.pot, invested=dict(terminal.invested), folded=frozenset())
        branch = ChanceBranch(card=Card("2", "c"), equity_table=np.array([[0.5]]), root=branch_showdown)
        return ChanceNode(pot=terminal.pot, invested=dict(terminal.invested), branches={branch.card: branch})

    solve(root, _ONE_HAND, _DUMMY_1X1_EQUITY_TABLE, iterations=50, chance_fn=_chance_fn)
    assert call_count[0] == 1


def test_solve_chance_data_supplied_by_caller_is_read_back():
    # chance_data mutates by reference — a caller-supplied dict should
    # end up holding the built ChanceNode, keyed by id(terminal), so a
    # caller can walk into a specific branch's subtree afterward.
    showdown_terminal = TerminalNode(pot=10.0, invested={BTN: 1.0, BB: 1.0}, folded=frozenset())
    fold_terminal = TerminalNode(pot=1.5, invested={BTN: 0.5, BB: 1.0}, folded=frozenset({BTN}))
    root = DecisionNode(
        player_to_act=BTN, pot=1.5, invested={BTN: 0.5, BB: 1.0}, folded=frozenset(), raises_so_far=0,
        children={Action(FOLD): fold_terminal, Action(RAISE, 1.0): showdown_terminal},
    )

    def _chance_fn(terminal):
        branch = ChanceBranch(
            card=Card("2", "c"), equity_table=np.array([[0.5]]),
            root=TerminalNode(pot=terminal.pot, invested=dict(terminal.invested), folded=frozenset()),
        )
        return ChanceNode(pot=terminal.pot, invested=dict(terminal.invested), branches={branch.card: branch})

    my_chance_data = {}
    solve(root, _ONE_HAND, _DUMMY_1X1_EQUITY_TABLE, iterations=10, chance_fn=_chance_fn, chance_data=my_chance_data)
    assert id(showdown_terminal) in my_chance_data
    assert isinstance(my_chance_data[id(showdown_terminal)], ChanceNode)


def test_solve_handles_a_chance_node_nested_two_levels_deep():
    # M13: proves cfr.py needs zero changes to support turn->river
    # chaining (chance.py's chain_to_river just populates a *second*
    # level of ChanceBranch.chance_fn — see its module docstring) by
    # hand-building a tree with a chance node nested inside another
    # chance node's own branch, using only the M12 mechanism already in
    # place, and confirming both levels dispatch correctly into the
    # *same* chance_data dict.
    fold_terminal = TerminalNode(pot=1.5, invested={BTN: 0.5, BB: 1.0}, folded=frozenset({BTN}))

    # Level 2 (river): a real DecisionNode so we can also confirm regret/
    # strategy accumulates correctly beneath the second chance node.
    river_fold = TerminalNode(pot=10.0, invested={BTN: 3.0, BB: 3.0}, folded=frozenset({BTN}))
    river_showdown = TerminalNode(pot=16.0, invested={BTN: 6.0, BB: 6.0}, folded=frozenset())
    river_decision = DecisionNode(
        player_to_act=BTN, pot=10.0, invested={BTN: 3.0, BB: 3.0}, folded=frozenset(), raises_so_far=0,
        children={Action(FOLD): river_fold, Action(RAISE, 6.0): river_showdown},
    )
    river_branch = ChanceBranch(card=Card("K", "s"), equity_table=np.array([[0.7]]), root=river_decision)

    # Level 1 (turn): a real DecisionNode whose own showdown terminal is
    # what triggers the level-2 (river) chance node, via the *inner*
    # chance_fn — not the outer, flop-scoped one (that per-branch
    # on/off switch is exactly M12's own scoping-bug fix).
    turn_showdown = TerminalNode(pot=10.0, invested={BTN: 3.0, BB: 3.0}, folded=frozenset())
    turn_decision = DecisionNode(
        player_to_act=BTN, pot=1.5, invested={BTN: 1.0, BB: 1.0}, folded=frozenset(), raises_so_far=0,
        children={Action(RAISE, 3.0): turn_showdown},
    )

    river_chance_calls = [0]

    def river_chance_fn(terminal):
        river_chance_calls[0] += 1
        assert terminal is turn_showdown  # dispatched from the correct (turn-level) terminal
        return ChanceNode(pot=terminal.pot, invested=dict(terminal.invested), branches={river_branch.card: river_branch})

    turn_branch = ChanceBranch(card=Card("2", "c"), equity_table=np.array([[0.6]]), root=turn_decision, chance_fn=river_chance_fn)
    flop_chance_node = ChanceNode(pot=1.5, invested={BTN: 1.0, BB: 1.0}, branches={turn_branch.card: turn_branch})

    flop_chance_calls = [0]

    def flop_chance_fn(terminal):
        flop_chance_calls[0] += 1
        return flop_chance_node

    flop_showdown_terminal = TerminalNode(pot=1.5, invested={BTN: 1.0, BB: 1.0}, folded=frozenset())
    root = DecisionNode(
        player_to_act=BTN, pot=1.5, invested={BTN: 0.5, BB: 1.0}, folded=frozenset(), raises_so_far=0,
        children={Action(FOLD): fold_terminal, Action(RAISE, 1.0): flop_showdown_terminal},
    )
    chance_data: dict = {}
    node_data = solve(
        root, _ONE_HAND, _DUMMY_1X1_EQUITY_TABLE, iterations=30, chance_fn=flop_chance_fn, chance_data=chance_data
    )

    # Both levels actually fired, exactly once each (memoized across all
    # 30 iterations) - and both landed in the *same* chance_data dict,
    # confirming M13's "flat, two-level dict" design needs no cfr.py
    # changes at all.
    assert flop_chance_calls[0] == 1
    assert river_chance_calls[0] == 1
    assert chance_data[id(flop_showdown_terminal)] is flop_chance_node
    assert chance_data[id(turn_showdown)].branches[river_branch.card].root is river_decision

    # A real DecisionNode beneath the *second* chance node still
    # accumulates well-formed regret/strategy, same as any other node.
    assert id(river_decision) in node_data
    avg = node_data[id(river_decision)].average_strategy()
    assert not np.any(np.isnan(avg))


# ---------------------------------------------------------------------------
# M31: mccfr_solve's initial_reach — per-position range seeding, used for
# BOTH the traverser's own reach AND how each opponent's hand is sampled.
# Phase 2 of docs/full-table-diagnostic-2026-08.md's recommendation #5
# ("true multiway postflop solving") — multiway_board_equity.py (M30) was
# phase 1. See mccfr_solve's own docstring for the two remaining
# prerequisites this phase still deliberately doesn't attempt (a
# board-aware equity source actually threaded through terminal-value
# computation, and a chance-branch sampling case in this module's own
# recursion).
# ---------------------------------------------------------------------------

_M31_HANDS = [
    StartingHand("A", "A"),
    StartingHand("K", "K"),
    StartingHand("Q", "Q"),
    StartingHand("7", "2", suited=False),
]


def test_mccfr_solve_default_initial_reach_matches_explicit_combo_weight():
    # The single most important regression guarantee for this change:
    # omitting initial_reach (or passing None, the default) must be
    # EXACTLY equivalent to every position explicitly supplying its own
    # combo_weight-derived array — proving every pre-M31 call site's
    # behavior is unaffected, not just "close." Shares one equity_cache
    # across both calls deliberately: MultiwayEquityCache memoizes each
    # opponent-tuple's value via a seed derived from the tuple itself
    # (not a shared advancing RNG), so a warm cache returns identical
    # values to a cold one — reuse here is a speed optimization, not a
    # correctness risk.
    config = GameConfig(positions=("BTN", "SB", "BB"))
    root = build_game_tree(config)
    equity_cache = MultiwayEquityCache(hands=_M31_HANDS, samples=100, seed=1)

    omitted = mccfr_solve(root, _M31_HANDS, config.positions, equity_cache, iterations=500, seed=5)

    combo_weights = [hand.combo_weight for hand in _M31_HANDS]
    explicit = mccfr_solve(
        root, _M31_HANDS, config.positions, equity_cache, iterations=500, seed=5,
        initial_reach={position: combo_weights for position in config.positions},
    )

    assert omitted.keys() == explicit.keys()
    for node_id in omitted:
        assert np.array_equal(omitted[node_id].regret_sum, explicit[node_id].regret_sum)
        assert np.array_equal(omitted[node_id].strategy_sum, explicit[node_id].strategy_sum)


def test_sample_opponent_hands_respects_per_position_weights():
    # SB's weight vector has ALL its mass on a single hand (QQ) — across
    # many independent draws, SB's sampled hand must ALWAYS be QQ, never
    # anything else, proving opponent sampling reads from THAT position's
    # own weight vector, not one shared distribution the way it did
    # before M31.
    hands = _M31_HANDS
    qq_index = hands.index(StartingHand("Q", "Q"))
    sb_weights = np.zeros(len(hands))
    sb_weights[qq_index] = 1.0
    default_weights = np.array([hand.combo_weight for hand in hands])
    position_weights = {"BTN": default_weights, "SB": sb_weights, "BB": default_weights}

    rng = random.Random(11)
    for _ in range(200):
        sampled = _sample_opponent_hands(("BTN", "SB", "BB"), "BTN", position_weights, hands, rng)
        assert sampled["SB"] == StartingHand("Q", "Q")
        assert sampled["BB"] in hands


def test_sample_opponent_hands_falls_back_to_last_draw_when_resampling_is_exhausted():
    # Every opponent's weight vector is degenerate to the SAME single
    # hand (AA) — three simultaneous AA opponents would need 6 aces, only
    # 4 exist, so every resample attempt is provably infeasible (M27's
    # own precheck, still load-bearing here). This must never hang or
    # raise — it returns the last (infeasible) draw, exactly like every
    # draw did before M27's resampling fix existed, just relocated into
    # this now-standalone, directly-testable helper.
    hands = [StartingHand("A", "A"), StartingHand("K", "K")]
    aa_only = np.array([1.0, 0.0])
    position_weights = {"BTN": np.array([0.5, 0.5]), "SB": aa_only, "BB": aa_only, "CO": aa_only}
    rng = random.Random(3)
    result = _sample_opponent_hands(("BTN", "SB", "BB", "CO"), "BTN", position_weights, hands, rng)
    assert result == {
        "SB": StartingHand("A", "A"),
        "BB": StartingHand("A", "A"),
        "CO": StartingHand("A", "A"),
    }


def test_mccfr_solve_traverser_reach_seeded_from_own_initial_reach():
    # BTN's initial_reach has ZERO weight on 72o. root.player_to_act is
    # BTN (visited on EVERY BTN-traversal iteration, no sampling
    # uncertainty — unlike a deeper node, which would only be reached
    # when some opponent's SAMPLED action happens to lead there), so this
    # directly isolates the traverser-reach-seeding half of M31 from the
    # opponent-sampling half already covered above. After solving, 72o
    # must show trained_mask()==False at root (zero reach weight — the
    # same real "trained=False" cause M28's own trained_hands docstring
    # already documents, engineered deliberately here rather than
    # incidentally discovered), while AA (real reach weight) must be
    # trained.
    config = GameConfig(positions=("BTN", "SB", "BB"))
    root = build_game_tree(config)
    assert root.player_to_act == "BTN"
    equity_cache = MultiwayEquityCache(hands=_M31_HANDS, samples=100, seed=1)

    weak_index = _M31_HANDS.index(StartingHand("7", "2", suited=False))
    aa_index = _M31_HANDS.index(StartingHand("A", "A"))
    btn_weights = np.array([hand.combo_weight for hand in _M31_HANDS])
    btn_weights[weak_index] = 0.0
    default_weights = [hand.combo_weight for hand in _M31_HANDS]

    node_data = mccfr_solve(
        root, _M31_HANDS, config.positions, equity_cache, iterations=300, seed=2,
        initial_reach={"BTN": btn_weights, "SB": default_weights, "BB": default_weights},
    )

    trained = node_data[id(root)].trained_mask()
    assert not trained[weak_index]
    assert trained[aa_index]


def test_mccfr_solve_initial_reach_wrong_shape_raises():
    config = GameConfig(positions=("BTN", "SB", "BB"))
    root = build_game_tree(config)
    equity_cache = MultiwayEquityCache(hands=_M31_HANDS, samples=50, seed=1)
    with pytest.raises(ValueError, match="shape"):
        mccfr_solve(
            root, _M31_HANDS, config.positions, equity_cache, iterations=10, seed=1,
            initial_reach={"BTN": [1.0, 1.0]},  # wrong length: 2, not len(_M31_HANDS)==4
        )


def test_mccfr_solve_initial_reach_all_zero_raises():
    config = GameConfig(positions=("BTN", "SB", "BB"))
    root = build_game_tree(config)
    equity_cache = MultiwayEquityCache(hands=_M31_HANDS, samples=50, seed=1)
    with pytest.raises(ValueError, match="sums to zero"):
        mccfr_solve(
            root, _M31_HANDS, config.positions, equity_cache, iterations=10, seed=1,
            initial_reach={"BTN": [0.0, 0.0, 0.0, 0.0]},
        )


def test_mccfr_solve_initial_reach_changes_result_from_default():
    # Same seed, same tree, same equity cache — the ONLY difference
    # between the two solves is BTN's own seeded range. If initial_reach
    # were silently ignored somewhere in the pipeline, these would come
    # back bit-identical; they must not.
    config = GameConfig(positions=("BTN", "SB", "BB"))
    root = build_game_tree(config)
    equity_cache = MultiwayEquityCache(hands=_M31_HANDS, samples=100, seed=1)

    default_result = mccfr_solve(root, _M31_HANDS, config.positions, equity_cache, iterations=300, seed=9)

    weak_index = _M31_HANDS.index(StartingHand("7", "2", suited=False))
    btn_weights = np.array([hand.combo_weight for hand in _M31_HANDS])
    btn_weights[weak_index] = 0.0
    default_weights = [hand.combo_weight for hand in _M31_HANDS]
    seeded_result = mccfr_solve(
        root, _M31_HANDS, config.positions, equity_cache, iterations=300, seed=9,
        initial_reach={"BTN": btn_weights, "SB": default_weights, "BB": default_weights},
    )

    assert not np.array_equal(
        default_result[id(root)].strategy_sum, seeded_result[id(root)].strategy_sum
    )


# ---------------------------------------------------------------------------
# M32: MCCFR chance-branch sampling + board-aware terminal equity (Phase 3
# of docs/full-table-diagnostic-2026-08.md's recommendation #5, closing it).
# Phase 1 (multiway_board_equity.py) was M30; Phase 2 (initial_reach, above)
# was M31. See cfr.py's own module docstring (item 4) and chance.py's
# SampledChanceBranch/build_mccfr_chance_branch for the full design.
# ---------------------------------------------------------------------------

_TOY_BOARD = (Card("2", "c"), Card("7", "d"), Card("9", "s"))
_TOY_COMBOS = [HandCombo(Card("A", "h"), Card("A", "d")), HandCombo(Card("K", "h"), Card("K", "d"))]


# --- _opponent_hands_are_dealable / _sample_opponent_hands with HandCombo ---
# (the hard blocker found during M32's own design: _sample_opponent_hands
# previously always called deal_n_hands, which reads StartingHand-only
# attributes and raises AttributeError on a HandCombo — confirmed by
# direct execution before this fix existed, not assumed.)


def test_opponent_hands_are_dealable_true_for_non_conflicting_combos():
    assert _opponent_hands_are_dealable(list(_TOY_COMBOS))


def test_opponent_hands_are_dealable_false_for_a_shared_card():
    conflicting = [_TOY_COMBOS[0], HandCombo(Card("A", "h"), Card("Q", "d"))]  # shares Ah with AA
    assert not _opponent_hands_are_dealable(conflicting)


def test_opponent_hands_are_dealable_true_for_empty_list():
    assert _opponent_hands_are_dealable([])


def test_opponent_hands_are_dealable_still_uses_deal_n_hands_for_starting_hand():
    # Byte-for-byte unchanged behavior for the pre-M32 (StartingHand) case
    # — the real regression guarantee this refactor depends on.
    assert _opponent_hands_are_dealable([StartingHand("A", "A"), StartingHand("K", "K")])
    assert not _opponent_hands_are_dealable([StartingHand("K", "K"), StartingHand("K", "K"), StartingHand("K", "K")])


def test_sample_opponent_hands_works_with_a_handcombo_pool():
    combos = [
        HandCombo(Card("A", "h"), Card("A", "d")),
        HandCombo(Card("K", "h"), Card("K", "d")),
        HandCombo(Card("Q", "h"), Card("Q", "d")),
    ]
    weights = np.array([1.0, 1.0, 1.0])
    position_weights = {"BTN": weights, "SB": weights, "BB": weights}
    rng = random.Random(5)
    for _ in range(50):
        sampled = _sample_opponent_hands(("BTN", "SB", "BB"), "BTN", position_weights, combos, rng)
        assert _opponent_hands_are_dealable(list(sampled.values()))  # no crash, and mutually legal


def test_sample_opponent_hands_resamples_when_handcombos_conflict():
    aa1 = HandCombo(Card("A", "h"), Card("A", "d"))
    aa2 = HandCombo(Card("A", "c"), Card("A", "s"))  # does not conflict with aa1
    kk = HandCombo(Card("K", "h"), Card("K", "d"))
    combos = [aa1, aa2, kk]
    position_weights = {
        "BTN": np.array([0.0, 0.0, 1.0]),
        "SB": np.array([1.0, 0.0, 0.0]),  # SB always draws aa1
        "BB": np.array([0.5, 0.5, 0.0]),  # BB conflicts with SB half the time (aa1), escapes half (aa2)
    }
    rng = random.Random(7)
    sampled = _sample_opponent_hands(("BTN", "SB", "BB"), "BTN", position_weights, combos, rng)
    # 0.5^50 chance of never escaping in MAX_OPPONENT_RESAMPLE_ATTEMPTS
    # tries — not truly flake-proof, but astronomically unlikely (~1e-15).
    assert sampled == {"SB": aa1, "BB": aa2}


# --- mccfr_solve backward compatibility + the new board/chance_fn/chance_data params ---


def test_mccfr_solve_without_chance_fn_matches_pre_m32_behavior():
    root, hands, equity_cache = _mccfr_toy_game(10.0, 1.0, 0.6)
    omitted = mccfr_solve(root, hands, positions=(BTN, BB), equity_cache=equity_cache, iterations=200, seed=11)
    explicit_none = mccfr_solve(
        root, hands, positions=(BTN, BB), equity_cache=equity_cache, iterations=200, seed=11,
        board=None, chance_fn=None, chance_data=None,
    )
    assert omitted.keys() == explicit_none.keys()
    for node_id in omitted:
        assert np.array_equal(omitted[node_id].regret_sum, explicit_none[node_id].regret_sum)
        assert np.array_equal(omitted[node_id].strategy_sum, explicit_none[node_id].strategy_sum)


def test_mccfr_solve_chance_fn_requires_board():
    root, hands, equity_cache = _mccfr_toy_game(10.0, 1.0, 0.6)
    with pytest.raises(ValueError, match="board"):
        mccfr_solve(
            root, hands, positions=(BTN, BB), equity_cache=equity_cache, iterations=10, seed=1,
            chance_fn=lambda terminal, card: None,
        )


# --- _sample_chance_card ---


def test_sample_chance_card_excludes_board_and_live_opponent_cards():
    opponent = HandCombo(Card("A", "h"), Card("A", "d"))
    rng = random.Random(3)
    for _ in range(300):
        card = _sample_chance_card(_TOY_BOARD, (opponent,), rng)
        assert card not in _TOY_BOARD
        assert card not in opponent.cards


# --- _mccfr_recurse's new chance dispatch ---


def test_mccfr_recurse_dispatches_via_chance_fn_for_a_contested_showdown_terminal():
    combos = _TOY_COMBOS
    hand_index = {h: i for i, h in enumerate(combos)}
    showdown_terminal = TerminalNode(pot=10.0, invested={"BTN": 1.0, "BB": 1.0}, folded=frozenset())
    root = DecisionNode(
        player_to_act="BTN", pot=1.5, invested={"BTN": 0.5, "BB": 1.0}, folded=frozenset(), raises_so_far=0,
        children={Action(RAISE, 1.0): showdown_terminal},
    )
    branch_root = TerminalNode(pot=10.0, invested={"BTN": 1.0, "BB": 1.0}, folded=frozenset())
    branch = SampledChanceBranch(
        card=Card("5", "s"), board=_TOY_BOARD + (Card("5", "s"),),
        equity_cache=_StubEquityCache(0.9, num_hands=2), root=branch_root, chance_fn=None,
    )
    calls = []

    def spy_chance_fn(terminal, card):
        calls.append((terminal, card))
        return branch

    value = _mccfr_recurse(
        root, "BTN", {"BB": combos[1]}, np.array([1.0, 1.0]), {}, 2, hand_index,
        _StubEquityCache(0.5, num_hands=2), random.Random(1),
        board=_TOY_BOARD, chance_fn=spy_chance_fn, chance_data={},
    )
    assert len(calls) == 1
    assert calls[0][0] is showdown_terminal
    # The real, arithmetic-level proof dispatch actually swapped equity
    # sources — not just that a spy got called: the value reflects the
    # BRANCH's own 0.9 equity, not the outer, ambient 0.5.
    assert np.allclose(value, 0.9 * 10.0 - 1.0)


def test_mccfr_recurse_does_not_dispatch_chance_when_traverser_has_folded():
    # A genuinely 3-handed terminal where the TRAVERSER folded but two
    # OTHER positions remain live — is_showdown is True (2 of 3 live), so
    # this directly tests the divergence from _solve_recurse's single
    # is_showdown gate: traverser-folded must still block dispatch.
    terminal = TerminalNode(pot=15.0, invested={"BTN": 1.0, "MID": 1.0, "IP": 1.0}, folded=frozenset({"BTN"}))
    assert terminal.is_showdown
    combos = _TOY_COMBOS
    hand_index = {h: i for i, h in enumerate(combos)}

    def spy_chance_fn(terminal, card):
        raise AssertionError("chance_fn must not be called when the traverser has folded")

    value = _mccfr_recurse(
        terminal, "BTN", {"MID": combos[1], "IP": combos[0]}, np.array([1.0, 1.0]), {}, 2, hand_index,
        _StubEquityCache(0.5, num_hands=2), random.Random(1),
        board=_TOY_BOARD, chance_fn=spy_chance_fn, chance_data={},
    )
    assert np.array_equal(value, np.full(2, -1.0))  # traverser folded: fixed -invested payoff


def test_mccfr_recurse_does_not_rechance_a_turn_level_showdown_terminal():
    # Mirrors test_solve_does_not_recurse_chance_fn_into_branch_subtrees's
    # own regression, adapted for sampling: a branch whose own root is
    # ITSELF a showdown terminal must fall through to direct equity, not
    # trigger a second dispatch back through the ambient chance_fn.
    combos = _TOY_COMBOS
    hand_index = {h: i for i, h in enumerate(combos)}
    turn_showdown = TerminalNode(pot=10.0, invested={"BTN": 1.0, "BB": 1.0}, folded=frozenset())
    flop_showdown = TerminalNode(pot=10.0, invested={"BTN": 1.0, "BB": 1.0}, folded=frozenset())
    root = DecisionNode(
        player_to_act="BTN", pot=1.5, invested={"BTN": 0.5, "BB": 1.0}, folded=frozenset(), raises_so_far=0,
        children={Action(RAISE, 1.0): flop_showdown},
    )
    branch = SampledChanceBranch(
        card=Card("5", "s"), board=_TOY_BOARD + (Card("5", "s"),),
        equity_cache=_StubEquityCache(0.9, num_hands=2), root=turn_showdown, chance_fn=None,
    )
    calls = []

    def spy_chance_fn(terminal, card):
        calls.append((terminal, card))
        return branch

    _mccfr_recurse(
        root, "BTN", {"BB": combos[1]}, np.array([1.0, 1.0]), {}, 2, hand_index,
        _StubEquityCache(0.5, num_hands=2), random.Random(1),
        board=_TOY_BOARD, chance_fn=spy_chance_fn, chance_data={},
    )
    assert len(calls) == 1
    assert calls[0][0] is flop_showdown


def test_mccfr_chance_dispatch_all_in_already_reuses_terminal_without_redispatch():
    combos = _TOY_COMBOS
    hand_index = {h: i for i, h in enumerate(combos)}
    all_in_terminal = TerminalNode(pot=30.0, invested={"BTN": 15.0, "BB": 15.0}, folded=frozenset())
    root = DecisionNode(
        player_to_act="BTN", pot=1.5, invested={"BTN": 0.5, "BB": 1.0}, folded=frozenset(), raises_so_far=0,
        children={Action(ALL_IN, 15.0): all_in_terminal},
    )
    # Mirrors build_mccfr_chance_branch's own all-in-already behavior:
    # branch.root IS the same terminal object, chance_fn is None.
    branch = SampledChanceBranch(
        card=Card("5", "s"), board=_TOY_BOARD + (Card("5", "s"),),
        equity_cache=_StubEquityCache(0.9, num_hands=2), root=all_in_terminal, chance_fn=None,
    )
    calls = []

    def spy_chance_fn(terminal, card):
        calls.append((terminal, card))
        return branch

    value = _mccfr_recurse(
        root, "BTN", {"BB": combos[1]}, np.array([1.0, 1.0]), {}, 2, hand_index,
        _StubEquityCache(0.5, num_hands=2), random.Random(1),
        board=_TOY_BOARD, chance_fn=spy_chance_fn, chance_data={},
    )
    assert len(calls) == 1  # dispatched exactly once, into the branch
    assert np.allclose(value, 0.9 * 30.0 - 15.0)


def test_mccfr_solve_chance_data_memoized_for_repeated_terminal_card_pair():
    combos = _TOY_COMBOS
    showdown = TerminalNode(pot=10.0, invested={"BTN": 1.0, "BB": 1.0}, folded=frozenset())
    root = DecisionNode(
        player_to_act="BTN", pot=1.5, invested={"BTN": 0.5, "BB": 1.0}, folded=frozenset(), raises_so_far=0,
        children={Action(RAISE, 1.0): showdown},
    )
    calls = []

    def spy_chance_fn(terminal, card):
        calls.append((terminal, card))
        return SampledChanceBranch(
            card=card, board=_TOY_BOARD + (card,), equity_cache=_StubEquityCache(0.5, num_hands=2),
            root=TerminalNode(pot=10.0, invested={"BTN": 1.0, "BB": 1.0}, folded=frozenset()), chance_fn=None,
        )

    equity_cache = _StubEquityCache(0.5, num_hands=2)
    weights = np.array([1.0, 1.0])
    chance_data: dict = {}
    mccfr_solve(
        root, combos, positions=(BTN, BB), equity_cache=equity_cache, iterations=100, seed=1,
        initial_reach={BTN: weights, BB: weights},
        board=_TOY_BOARD, chance_fn=spy_chance_fn, chance_data=chance_data,
    )
    # At most 52-3=49 distinct cards can ever be sampled for this one
    # terminal — the spy's call count must equal distinct (terminal, card)
    # pairs actually dispatched, never the iteration count (100).
    assert 0 < len(calls) <= 49
    assert len(calls) == len(chance_data)
    # every call was for a genuinely distinct (terminal, card) pair — keyed
    # the same way chance_data itself is keyed (TerminalNode isn't
    # hashable, since it carries a dict field, so id() stands in for it)
    seen_keys = {(id(terminal), card) for terminal, card in calls}
    assert len(seen_keys) == len(calls)


# --- _mccfr_terminal_value's NaN handling (M32 shape, M66 semantics) ---


class _NanStubEquityCache:
    """Returns equity with a NaN in a specific slot — for testing
    _mccfr_terminal_value's handling of an unpriceable hand directly."""

    def __init__(self, num_hands, nan_index):
        self._num_hands = num_hands
        self._nan_index = nan_index

    def traverser_equity_vector(self, opponent_hands):
        vector = np.full(self._num_hands, 0.7)
        vector[self._nan_index] = np.nan
        return vector


def test_mccfr_terminal_value_preserves_nan_for_an_unpriceable_hand():
    # M32 replaced NaN with a neutral 0.5 here; M66 deliberately does not,
    # because that 0.5 is exactly the kind of fabricated value CFR+'s
    # regret floor ratchets on. NaN is now the signal that says "this hand
    # had no real value this iteration" and it must survive to reach
    # _mccfr_recurse, which turns it into a SKIPPED update.
    combos = _TOY_COMBOS
    terminal = TerminalNode(pot=10.0, invested={"BTN": 1.0, "BB": 1.0}, folded=frozenset())
    cache = _NanStubEquityCache(num_hands=2, nan_index=0)
    value = _mccfr_terminal_value(terminal, "BTN", {"BB": combos[1]}, 2, cache)
    assert np.isnan(value[0])  # the unpriceable hand stays unpriceable
    assert value[1] == pytest.approx(0.7 * 10.0 - 1.0)  # the real hand -> untouched


def test_mccfr_terminal_value_leaves_a_never_nan_cache_untouched():
    combos = _TOY_COMBOS
    terminal = TerminalNode(pot=10.0, invested={"BTN": 1.0, "BB": 1.0}, folded=frozenset())
    cache = _StubEquityCache(0.6, num_hands=2)
    value = _mccfr_terminal_value(terminal, "BTN", {"BB": combos[1]}, 2, cache)
    assert np.allclose(value, 0.6 * 10.0 - 1.0)


def test_mccfr_terminal_value_folds_a_validity_mask_into_nan():
    # MultiwayEquityCache can't report "no real value" in-band (it returns
    # a dense float array), so it reports out-of-band via
    # traverser_validity_mask. _mccfr_terminal_value folds that into the
    # same NaN representation NwayBoardEquityCache uses natively, so both
    # equity sources reach _mccfr_recurse looking identical.
    class _MaskedStub:
        def traverser_equity_vector(self, opponent_hands):
            return np.array([0.7, 0.7])

        def traverser_validity_mask(self, opponent_hands):
            return np.array([False, True])  # hand 0's value is fabricated

    combos = _TOY_COMBOS
    terminal = TerminalNode(pot=10.0, invested={"BTN": 1.0, "BB": 1.0}, folded=frozenset())
    value = _mccfr_terminal_value(terminal, "BTN", {"BB": combos[1]}, 2, _MaskedStub())
    assert np.isnan(value[0])
    assert value[1] == pytest.approx(0.7 * 10.0 - 1.0)


# --- M69: linear averaging of the time-averaged strategy ---


def test_linear_averaging_weights_later_iterations_more():
    """The mechanism, asserted directly.

    `strategy_weight` is applied ONLY to strategy_sum — regret updates and
    therefore the sampled traversal itself are untouched — so for a given
    seed the two runs walk the identical tree and differ solely in how
    each iteration's contribution is weighted. That makes the property
    exact rather than statistical: every per-iteration term is identical,
    so the linearly-weighted total must exceed the equally-weighted one
    and cannot exceed it by more than a factor of `iterations`.

    Deliberately NOT asserting that linear averaging moves the average
    further from uniform — that seems intuitive but is false in general,
    and was measured false on this very fixture (spread 0.859 vs 0.996).
    The real behavioural claim lives on a 6-max 169-class solve and is
    recorded with its measurements in mccfr_solve's own docstring.
    """
    combos = _TOY_COMBOS
    iterations = 80
    config = StreetConfig(positions=("BTN", "BB"), pot=10.0, stack_bb=20.0,
                          raise_sizes=(), max_raises=1)
    reach = {"BTN": np.ones(2), "BB": np.ones(2)}

    # One tree for both runs: node_data is keyed by id(node), so a fresh
    # tree per run would make the two dicts unmatchable. mccfr_solve
    # mutates node_data, never the tree, so sharing the root is safe.
    root = build_street_tree(config)

    def solve(linear):
        return mccfr_solve(root, combos, ("BTN", "BB"), _StubEquityCache(0.9, num_hands=2),
                           iterations=iterations, seed=3, initial_reach=reach,
                           linear_averaging=linear)

    equal_data, linear_data = solve(False), solve(True)
    assert equal_data and len(equal_data) == len(linear_data)

    for key, equal_table in equal_data.items():
        linear_table = linear_data[key]
        # The traversal is identical, so regrets must match exactly.
        assert np.array_equal(equal_table.regret_sum, linear_table.regret_sum)
        equal_total = equal_table.strategy_sum.sum()
        linear_total = linear_table.strategy_sum.sum()
        assert linear_total > equal_total
        assert linear_total <= iterations * equal_total
        # Both remain valid distributions with nothing corrupted.
        assert np.allclose(linear_table.average_strategy().sum(axis=1), 1.0)
        assert not np.any(np.isnan(linear_table.strategy_sum))


def test_linear_averaging_can_be_disabled_for_the_pre_m69_behaviour():
    """The flag must actually be a switch, not decoration — a caller that
    needs the old time-average can still get it, bit for bit."""
    combos = _TOY_COMBOS
    config = StreetConfig(positions=("BTN", "BB"), pot=10.0, stack_bb=20.0,
                          raise_sizes=(), max_raises=1)
    reach = {"BTN": np.ones(2), "BB": np.ones(2)}

    def solve(linear):
        root = build_street_tree(config)
        data = mccfr_solve(root, combos, ("BTN", "BB"), _StubEquityCache(0.9, num_hands=2),
                           iterations=60, seed=7, initial_reach=reach,
                           linear_averaging=linear)
        return [t.average_strategy() for t in data.values()]

    first, second = solve(False), solve(False)
    assert all(np.array_equal(a, b) for a, b in zip(first, second))
    assert any(not np.allclose(a, b) for a, b in zip(solve(False), solve(True)))


# --- M66: a fabricated value must produce NO learning, not wrong learning ---


def test_mccfr_skips_the_regret_update_for_an_unpriceable_hand():
    """The core M66 guarantee. A hand whose value is fabricated this
    iteration must leave regret_sum and strategy_sum completely untouched
    — not merely be nudged by a neutral number.

    This is what makes CFR+ safe here: its regret floor means regret only
    ever ratchets up, so any persistent one-sided bias accumulates forever
    instead of averaging out. Contributing nothing is the only update that
    can't accumulate.
    """
    combos = _TOY_COMBOS
    config = StreetConfig(positions=("BTN", "BB"), pot=10.0, stack_bb=20.0,
                          raise_sizes=(), max_raises=1)
    root = build_street_tree(config)
    # Hand 0 is never priceable; hand 1 always is.
    cache = _NanStubEquityCache(num_hands=2, nan_index=0)
    # HandCombo has no combo_weight, so every position's reach is explicit.
    reach = {"BTN": np.ones(2), "BB": np.ones(2)}
    node_data = mccfr_solve(root, combos, ("BTN", "BB"), cache, iterations=25, seed=1,
                            initial_reach=reach)

    assert node_data, "the solve should have touched at least one node"
    for table in node_data.values():
        assert not np.any(np.isnan(table.regret_sum)), "NaN must never enter regret_sum"
        assert not np.any(np.isnan(table.strategy_sum)), "NaN must never enter strategy_sum"
        # Hand 0 was never priceable, so it must have learned nothing at all...
        assert np.all(table.regret_sum[0] == 0.0)
        assert np.all(table.strategy_sum[0] == 0.0)
        # ...and must therefore honestly report itself as untrained (M28),
        # rather than carrying a strategy derived from a placeholder.
        assert table.trained_mask()[0] == False  # noqa: E712 - numpy bool


def test_mccfr_still_learns_normally_for_hands_that_are_always_priceable():
    """The other half: masking must not suppress learning for hands that
    DO have real values — otherwise it would 'fix' divergence by simply
    refusing to learn anything."""
    combos = _TOY_COMBOS
    config = StreetConfig(positions=("BTN", "BB"), pot=10.0, stack_bb=20.0,
                          raise_sizes=(), max_raises=1)
    root = build_street_tree(config)
    cache = _NanStubEquityCache(num_hands=2, nan_index=0)
    # HandCombo has no combo_weight, so every position's reach is explicit.
    reach = {"BTN": np.ones(2), "BB": np.ones(2)}
    node_data = mccfr_solve(root, combos, ("BTN", "BB"), cache, iterations=25, seed=1,
                            initial_reach=reach)

    assert any(table.trained_mask()[1] for table in node_data.values()), (
        "hand 1 is priceable at every terminal and must actually learn"
    )


# --- Determinism with chance dispatch active ---


def test_mccfr_solve_with_chance_fn_is_deterministic_given_a_seed():
    combos = _TOY_COMBOS
    showdown = TerminalNode(pot=10.0, invested={"BTN": 1.0, "BB": 1.0}, folded=frozenset())
    root = DecisionNode(
        player_to_act="BTN", pot=1.5, invested={"BTN": 0.5, "BB": 1.0}, folded=frozenset(), raises_so_far=0,
        children={Action(RAISE, 1.0): showdown},
    )

    def chance_fn(terminal, card):
        return SampledChanceBranch(
            card=card, board=_TOY_BOARD + (card,), equity_cache=_StubEquityCache(0.6, num_hands=2),
            root=TerminalNode(pot=10.0, invested={"BTN": 1.0, "BB": 1.0}, folded=frozenset()), chance_fn=None,
        )

    equity_cache = _StubEquityCache(0.5, num_hands=2)
    weights = np.array([1.0, 1.0])
    kwargs = dict(
        root=root, hands=combos, positions=(BTN, BB), equity_cache=equity_cache, iterations=200, seed=17,
        initial_reach={BTN: weights, BB: weights}, board=_TOY_BOARD, chance_fn=chance_fn,
    )
    first = mccfr_solve(**kwargs)
    second = mccfr_solve(**kwargs)
    assert np.array_equal(first[id(root)].regret_sum, second[id(root)].regret_sum)
    assert np.array_equal(first[id(root)].strategy_sum, second[id(root)].strategy_sum)


# --- The required end-to-end test: a real 3-max flop chaining into a real turn ---

_E2E_BOARD = (Card("2", "c"), Card("7", "d"), Card("9", "s"))
_E2E_POSITIONS = ("OOP", "MID", "IP")


def _e2e_combo_pool():
    # 2 combos per position, on ranks disjoint from the board and each
    # other, so a weight-restricted per-position range is meaningful (a
    # position's own weight vector is 0 everywhere except its own combos).
    oop = [HandCombo(Card("A", "h"), Card("A", "d")), HandCombo(Card("K", "h"), Card("K", "d"))]
    mid = [HandCombo(Card("Q", "h"), Card("Q", "d")), HandCombo(Card("J", "h"), Card("J", "d"))]
    ip = [HandCombo(Card("T", "h"), Card("T", "d")), HandCombo(Card("6", "h"), Card("6", "d"))]
    return oop, mid, ip


def test_mccfr_solve_real_3max_flop_chains_into_turn_and_produces_well_formed_strategy():
    oop, mid, ip = _e2e_combo_pool()
    all_combos = oop + mid + ip

    def _weights_for(own_combos):
        return np.array([1.0 if c in own_combos else 0.0 for c in all_combos])

    initial_reach = {"OOP": _weights_for(oop), "MID": _weights_for(mid), "IP": _weights_for(ip)}

    config = StreetConfig(positions=_E2E_POSITIONS, pot=6.0, stack_bb=20.0, raise_sizes=(), max_raises=1)
    root = build_street_tree(config)
    equity_cache = NwayBoardEquityCache(_E2E_BOARD, all_combos)

    def chance_fn(terminal, card):
        return build_mccfr_chance_branch(
            terminal, card=card, board=_E2E_BOARD, combos=all_combos, positions=_E2E_POSITIONS,
            effective_stack_bb=20.0, raise_sizes=(), max_raises=1,
        )

    chance_data: dict = {}
    node_data = mccfr_solve(
        root, all_combos, positions=_E2E_POSITIONS, equity_cache=equity_cache, iterations=300, seed=1,
        initial_reach=initial_reach, board=_E2E_BOARD, chance_fn=chance_fn, chance_data=chance_data,
    )

    assert node_data
    for table in node_data.values():
        avg = table.average_strategy()
        assert not np.any(np.isnan(avg))
        assert np.allclose(avg.sum(axis=1), 1.0)

    assert len(chance_data) > 0  # dispatch actually fired at least once
    assert any(isinstance(branch.root, DecisionNode) for branch in chance_data.values())  # a real turn decision


# --- M97: predictive regret matching, for M74's bang-bang oscillation ---


def test_optimism_defaults_to_off_and_leaves_the_policy_untouched():
    """The default must be exactly the pre-M97 rule. `last_regret` is now
    stored on every update whether or not anyone reads it, so the flag has
    to be what changes behaviour — not the presence of the field."""
    table = InfoSetTable.zeros(2, 3)
    table.regret_sum = np.array([[3.0, 1.0, 0.0], [0.0, 0.0, 0.0]])
    table.last_regret = np.array([[-3.0, 9.0, 0.0], [5.0, 0.0, 0.0]])

    plain = table.current_strategy()
    assert np.allclose(plain[0], [0.75, 0.25, 0.0])
    # Row 1 has no positive regret at all, so it falls back to uniform —
    # and must NOT be rescued by last_regret while optimism is off.
    assert np.allclose(plain[1], [1 / 3, 1 / 3, 1 / 3])
    assert np.array_equal(plain, table.current_strategy(0.0))


def test_optimism_adds_one_more_copy_of_the_last_instantaneous_regret():
    """The rule itself: match against `regret_sum + optimism *
    last_regret`. Asserted against hand-computed numbers rather than
    against the implementation, so a sign slip or a double-count fails."""
    table = InfoSetTable.zeros(1, 2)
    table.regret_sum = np.array([[4.0, 6.0]])
    table.last_regret = np.array([[6.0, -2.0]])

    # regret_sum + 1.0 * last_regret = [10, 4] -> [0.714..., 0.285...]
    assert np.allclose(table.current_strategy(1.0), [[10 / 14, 4 / 14]])
    # Half a step: [7, 5] -> [0.583..., 0.416...]
    assert np.allclose(table.current_strategy(0.5), [[7 / 12, 5 / 12]])
    # And the un-predicted policy is a different answer, so the test is
    # not passing on a coincidence.
    assert not np.allclose(table.current_strategy(0.0), table.current_strategy(1.0))


def test_optimism_reacts_before_plain_regret_matching_does():
    """Why the parameter exists, as a property rather than a number.

    An action that has just started losing badly is still the leader by
    accumulated regret for a while, so plain regret matching keeps playing
    it. Prediction lets the policy move on the evidence it already has —
    which is the damping M74 said the oscillation needs.
    """
    table = InfoSetTable.zeros(1, 2)
    table.regret_sum = np.array([[10.0, 8.0]])   # action 0 still ahead
    table.last_regret = np.array([[-6.0, 6.0]])  # but it just collapsed

    plain = table.current_strategy(0.0)[0]
    predicted = table.current_strategy(1.0)[0]
    assert plain[0] > plain[1], "fixture is wrong: action 0 should still lead on the sum"
    assert predicted[1] > predicted[0], "prediction did not react to the fresh regret"


def test_optimism_never_produces_an_invalid_distribution():
    """A prediction can drive every action's score negative, where the sum
    is zero and the naive normalization is 0/0. That path must fall back to
    uniform exactly as the un-predicted one does."""
    table = InfoSetTable.zeros(1, 3)
    table.regret_sum = np.array([[1.0, 2.0, 3.0]])
    table.last_regret = np.array([[-50.0, -50.0, -50.0]])
    strategy = table.current_strategy(1.0)
    assert np.allclose(strategy, 1 / 3)
    assert not np.any(np.isnan(strategy))


def _optimism_toy(equity, optimism, seed=11, iterations=60):
    config = StreetConfig(positions=("BTN", "BB"), pot=10.0, stack_bb=20.0,
                          raise_sizes=(), max_raises=1)
    root = build_street_tree(config)
    data = mccfr_solve(root, _TOY_COMBOS, ("BTN", "BB"),
                       _StubEquityCache(equity, num_hands=2), iterations=iterations,
                       seed=seed, initial_reach={"BTN": np.ones(2), "BB": np.ones(2)},
                       optimism=optimism)
    return [table.average_strategy() for table in data.values()]


def test_mccfr_solve_threads_optimism_all_the_way_down():
    """End to end: the parameter must reach the policy inside the
    traversal, not merely sit on the signature.

    The fixture has to be NEAR-TIED (equity 0.4) and that is the whole
    point. At equity 0.9 the two arms are bit-identical — one action's
    regret is deeply negative every iteration, so adding another copy of
    it cannot change which side of zero it lands on, and the policy is
    (0, 1) either way. A dominated fixture makes this parameter a
    provable no-op, so a test written on one would pass whether or not
    the plumbing worked at all. Optimism only does anything where the
    actions genuinely compete — exactly M74's regime.
    """
    assert any(
        not np.allclose(a, b)
        for a, b in zip(_optimism_toy(0.4, 0.0), _optimism_toy(0.4, 1.0))
    ), "optimism reached nothing — the parameter is not threaded through"
    # Deterministic given a seed, in both modes.
    assert all(
        np.array_equal(a, b)
        for a, b in zip(_optimism_toy(0.4, 1.0), _optimism_toy(0.4, 1.0))
    )
    # And still a valid strategy, not just a different one.
    for average in _optimism_toy(0.4, 1.0):
        assert np.allclose(average.sum(axis=1), 1.0)
        assert not np.any(np.isnan(average))


def test_optimism_is_a_no_op_where_one_action_dominates():
    """The flip side, asserted rather than left as folklore: where a
    strictly worse action is losing by a wide margin, prediction cannot
    resurrect it, and the two arms agree bit for bit. Recorded so nobody
    writes an optimism test on a lopsided fixture and concludes the
    feature is broken."""
    assert all(
        np.array_equal(a, b)
        for a, b in zip(_optimism_toy(0.9, 0.0), _optimism_toy(0.9, 1.0))
    )


def _solve_for_history(optimism=0.0, smoothing=0.0):
    config = StreetConfig(positions=("BTN", "BB"), pot=10.0, stack_bb=20.0,
                          raise_sizes=(), max_raises=1)
    root = build_street_tree(config)
    return mccfr_solve(root, _TOY_COMBOS, ("BTN", "BB"), _StubEquityCache(0.4, num_hands=2),
                       iterations=20, seed=5,
                       initial_reach={"BTN": np.ones(2), "BB": np.ones(2)},
                       optimism=optimism, smoothing=smoothing)


def test_the_extra_history_arrays_cost_nothing_when_nobody_reads_them():
    """The default solve must not pay for M97 at all.

    `last_regret` and `last_strategy` are two more (num_hands,
    num_actions) arrays beside `regret_sum` and `strategy_sum` — storing
    them always would exactly DOUBLE `node_data`, the largest structure
    any solve produces and the one M93 had just finished bounding. Since
    both modes measured out as not worth enabling, the default path keeps
    neither.
    """
    data = _solve_for_history()
    assert data, "fixture solved nothing"
    assert all(t.last_regret is None for t in data.values())
    assert all(t.last_strategy is None for t in data.values())


def test_each_history_array_is_recorded_when_its_own_mode_is_on():
    """...and is really there when asked for, with the right shape —
    otherwise the modes above would silently degrade to plain regret
    matching and their measurements would mean nothing."""
    with_optimism = _solve_for_history(optimism=1.0)
    recorded = [t for t in with_optimism.values() if t.last_regret is not None]
    assert recorded, "optimism is on but no table recorded last_regret"
    assert all(t.last_regret.shape == t.regret_sum.shape for t in recorded)
    # Each mode stores only its own array, not the other's.
    assert all(t.last_strategy is None for t in with_optimism.values())

    with_smoothing = _solve_for_history(smoothing=0.9)
    played = [t for t in with_smoothing.values() if t.last_strategy is not None]
    assert played, "smoothing is on but no table recorded last_strategy"
    assert all(t.last_strategy.shape == t.regret_sum.shape for t in played)
    assert all(t.last_regret is None for t in with_smoothing.values())


def test_smoothing_moves_the_policy_in_steps_rather_than_wholesale():
    """The damping property itself, at the table level: a policy blended
    with the one just played cannot jump the whole way to the fresh
    regret-matching answer in a single step. That is the entire mechanism
    M74 asked for."""
    table = InfoSetTable.zeros(1, 2)
    table.regret_sum = np.array([[0.0, 5.0]])       # fresh answer: (0, 1)
    table.last_strategy = np.array([[1.0, 0.0]])    # but it just played (1, 0)

    fresh = table.current_strategy()
    assert np.allclose(fresh, [[0.0, 1.0]]), "fixture is wrong: fresh answer should be pure"

    damped = table.current_strategy(smoothing=0.9)
    assert np.allclose(damped, [[0.9, 0.1]])
    assert np.allclose(damped.sum(axis=1), 1.0)
    # Heavier smoothing must move less, not more.
    lighter = table.current_strategy(smoothing=0.5)
    assert lighter[0, 1] > damped[0, 1]


# --- M100: continuation value at terminals that still have chips behind ---


def _terminal_with_chips_behind(pot, invested, folded=frozenset()):
    return TerminalNode(pot=pot, invested=invested, folded=folded)


class _FixedEquity:
    """Equity cache stub returning a fixed vector, so the terminal payoff
    is a pure function of the arithmetic under test."""

    def __init__(self, values):
        self.values = np.asarray(values, dtype=float)

    def traverser_equity_vector(self, opponent_hands):
        return self.values


def test_continuation_defaults_to_off_and_changes_nothing():
    """The default must be byte-identical to the pre-M100 payoff, or every
    measurement taken before this milestone silently stops applying."""
    node = _terminal_with_chips_behind(20.0, {"BTN": 10.0, "BB": 10.0})
    cache = _FixedEquity([0.9, 0.1])
    plain = _mccfr_terminal_value(node, "BTN", {"BB": "x"}, 2, cache)
    assert np.allclose(plain, [0.9 * 20 - 10, 0.1 * 20 - 10])
    # Passing a stack but no coefficient must also be a no-op.
    assert np.allclose(
        _mccfr_terminal_value(node, "BTN", {"BB": "x"}, 2, cache, continuation=0.0, stack_bb=100.0),
        plain,
    )


def test_continuation_rewards_an_edge_only_where_chips_remain():
    """The asymmetry that IS the defect (M98): an all-in terminal has
    nothing behind and is already priced correctly, so it must be left
    alone, while a small bet gets the postflop value it was never given.

    If the correction applied to both, it would shift every line equally
    and could not remove a bias that exists only between them.
    """
    cache = _FixedEquity([0.9, 0.1])
    opponents = {"BB": "x"}

    # A small bet: 10 invested of a 100bb stack, so 90 behind.
    small = _terminal_with_chips_behind(20.0, {"BTN": 10.0, "BB": 10.0})
    corrected = _mccfr_terminal_value(small, "BTN", opponents, 2, cache,
                                      continuation=1.0, stack_bb=100.0)
    plain = _mccfr_terminal_value(small, "BTN", opponents, 2, cache)
    # +1.0 * (0.9 - 0.5) * 90 for the strong hand, and the mirror for the weak one.
    assert np.allclose(corrected, plain + np.array([0.4 * 90, -0.4 * 90]))

    # An all-in: the whole stack is in, nothing behind, nothing added.
    allin = _terminal_with_chips_behind(200.0, {"BTN": 100.0, "BB": 100.0})
    assert np.allclose(
        _mccfr_terminal_value(allin, "BTN", opponents, 2, cache, continuation=1.0, stack_bb=100.0),
        _mccfr_terminal_value(allin, "BTN", opponents, 2, cache),
    )


def test_continuation_is_zero_sum_at_equal_stacks():
    """It must not quietly create or destroy chips — otherwise a shift in
    the jam frequency could just be the correction paying everyone, which
    would look like a fix and be an artifact.

    Zero-sum holds by construction when stacks are equal: equities sum to
    1 across the live players, so the per-player terms sum to
    `(1 - n * 1/n) * behind == 0`. Asserted rather than argued.
    """
    node = _terminal_with_chips_behind(20.0, {"BTN": 10.0, "BB": 10.0})
    # One hand each; BTN holds the 0.7 side, BB the complementary 0.3.
    btn = _mccfr_terminal_value(node, "BTN", {"BB": "x"}, 1, _FixedEquity([0.7]),
                                continuation=0.5, stack_bb=100.0)
    bb = _mccfr_terminal_value(node, "BB", {"BTN": "x"}, 1, _FixedEquity([0.3]),
                               continuation=0.5, stack_bb=100.0)
    assert btn + bb == pytest.approx(0.0)


def test_continuation_leaves_a_folded_player_untouched():
    """A player who folded has no equity and no future — their payoff is
    exactly what they put in, correction or not."""
    node = _terminal_with_chips_behind(20.0, {"BTN": 10.0, "BB": 10.0},
                                       folded=frozenset({"BTN"}))
    value = _mccfr_terminal_value(node, "BTN", {"BB": "x"}, 2, _FixedEquity([0.9, 0.1]),
                                  continuation=1.0, stack_bb=100.0)
    assert np.allclose(value, [-10.0, -10.0])


# ---------------------------------------------------------------------------
# M161: vector CFR. `_solve_recurse` propagates an N-vector where
# `_solve_recurse_matrix` propagated an N x N matrix. The rewrite is a
# performance change and must not be a behaviour change, so the incumbent
# is kept runnable and these drive both through solve() itself.
# ---------------------------------------------------------------------------


def _equivalence_tree(num_hands=6, seed=11):
    """A real multi-level street tree plus a deterministic equity table."""
    rng = np.random.default_rng(seed)
    upper = rng.random((num_hands, num_hands))
    equity_table = (upper + (1.0 - upper.T)) / 2.0  # E[i,j] + E[j,i] == 1
    config = StreetConfig(
        positions=("OOP", "IP"), pot=6.0, stack_bb=20.0,
        raise_sizes=(2.5, 3.0), max_raises=3,
    )
    root = build_street_tree(config)
    hands = list(range(num_hands))
    weights = np.linspace(0.4, 1.0, num_hands)
    reach = {"OOP": weights, "IP": weights}
    return root, hands, equity_table, reach


def _worst_strategy_gap(node_data_a, node_data_b, root):
    """Compared over the nodes of `root`'s own tree, which both arms share.

    Not over the raw dicts: a chance_fn builds a fresh branch subtree per
    solve, so those nodes have different `id()`s in each arm and the key
    sets legitimately differ. The flop tree is the same object in both.
    """
    from poker_solver.game_tree import walk

    keys = [id(n) for n in walk(root) if id(n) in node_data_a]
    assert keys, "no shared decision nodes to compare"
    assert all(k in node_data_b for k in keys)
    return max(
        float(np.abs(node_data_a[k].average_strategy()
                     - node_data_b[k].average_strategy()).max())
        for k in keys
    )


def test_the_vector_recursion_matches_the_matrix_one():
    # The rewrite's whole claim. Run in float64 deliberately: the two
    # implementations do the same arithmetic in a different ORDER, and CFR
    # amplifies a rounding difference (M74's bang-bang behaviour swings a
    # near-tied node wholesale), so float32 measures chaos rather than
    # correctness. At double precision the gap is machine epsilon.
    root, hands, equity_table, reach = _equivalence_tree()
    common = dict(iterations=120, positions=("OOP", "IP"), initial_reach=reach)
    matrix = solve(root, hands, equity_table,
                   _recurse=cfr._solve_recurse_matrix, **common)
    vector = solve(root, hands, equity_table, **common)
    assert _worst_strategy_gap(matrix, vector, root) < 1e-9


def _chance_equivalence_tree(num_hands=4, seed=5):
    """A tree whose showdown terminals chain into a further street.

    The point is that the branch streets start from DIFFERENT pots
    (each terminal carries its own), so the dead money at a leaf varies
    across the tree — the condition under which the two recursions can
    disagree at all. See `_terminal_value_vector` for why.
    """
    rng = np.random.default_rng(seed)
    upper = rng.random((num_hands, num_hands))
    equity_table = (upper + (1.0 - upper.T)) / 2.0
    config = StreetConfig(
        positions=("OOP", "IP"), pot=6.0, stack_bb=12.0,
        raise_sizes=(2.5,), max_raises=2,
    )
    root = build_street_tree(config)

    def chance_fn(terminal):
        behind = 12.0 - max(terminal.invested.values())
        branches = {}
        for i, rank in enumerate(("2", "7")):
            card = Card(rank, "c")
            sub = StreetConfig(
                positions=("OOP", "IP"), pot=terminal.pot,
                stack_bb=max(behind, 1.0), raise_sizes=(2.5,), max_raises=2,
            )
            shifted = np.clip(equity_table + (0.05 * (i + 1)), 0.0, 1.0)
            branches[card] = ChanceBranch(
                card=card, equity_table=shifted, root=build_street_tree(sub)
            )
        return ChanceNode(pot=terminal.pot, invested=dict(terminal.invested),
                          branches=branches)

    hands = list(range(num_hands))
    weights = np.linspace(0.5, 1.0, num_hands)
    return root, hands, equity_table, {"OOP": weights, "IP": weights}, chance_fn


def test_the_vector_recursion_matches_the_matrix_one_across_a_chance_node():
    # The case a single-street check cannot see, and the one that caught a
    # real error in this rewrite before it shipped.
    #
    # `node.pot` includes dead money carried in from earlier streets, so a
    # terminal's two payoffs sum to that dead pot rather than to zero. The
    # matrix recursion values the second position as MINUS the first's,
    # which offsets it by exactly that dead pot. Within one street the
    # offset is the same at every terminal and cancels out of every regret
    # difference. Across a chance node into a street whose starting pot
    # depends on how much was bet to reach it, it does not cancel — a
    # first version of this rewrite computed the second player's true
    # payoff instead and moved strategies by 0.97, dtype-independent.
    root, hands, equity_table, reach, chance_fn = _chance_equivalence_tree()
    common = dict(iterations=60, positions=("OOP", "IP"), initial_reach=reach)
    matrix = solve(root, hands, equity_table, chance_fn=chance_fn, chance_data={},
                   _recurse=cfr._solve_recurse_matrix, **common)
    vector = solve(root, hands, equity_table, chance_fn=chance_fn, chance_data={},
                   **common)
    assert _worst_strategy_gap(matrix, vector, root) < 1e-9


def test_the_vector_terminal_reproduces_the_matrix_dead_pot_convention():
    # Pins the convention itself rather than its downstream effect, for
    # both traversers, at a terminal with real dead money (pot 10 against
    # 2 + 2 invested, so 6 is carried in). Asserting equality with the
    # matrix expression is the point: the second position's value here is
    # NOT that player's own payoff, and a future change that "corrects"
    # it should have to delete this test on purpose.
    equity_table = np.array([[0.25, 0.75], [0.5, 0.5]])
    reach_opp = np.array([0.6, 0.9])
    showdown = TerminalNode(pot=10.0, invested={"OOP": 2.0, "IP": 2.0},
                            folded=frozenset())
    folded = TerminalNode(pot=8.0, invested={"OOP": 2.0, "IP": 0.0},
                          folded=frozenset({"IP"}))
    for node in (showdown, folded):
        matrix = cfr._terminal_value_matrix(node, equity_table, "OOP", "IP")
        as_a = cfr._terminal_value_vector(node, equity_table, "OOP", "IP", True,
                                          reach_opp)
        as_b = cfr._terminal_value_vector(node, equity_table, "OOP", "IP", False,
                                          reach_opp)
        assert np.allclose(as_a, matrix @ reach_opp)
        assert np.allclose(as_b, (-matrix).T @ reach_opp)

    # And the property that makes the convention worth pinning: the two
    # do not sum to zero, they sum to the dead pot.
    a_payoff = equity_table * showdown.pot - showdown.invested["OOP"]
    b_payoff = (1.0 - equity_table) * showdown.pot - showdown.invested["IP"]
    assert np.allclose(a_payoff + b_payoff, 6.0)


def test_the_vector_recursion_returns_a_vector_not_a_matrix():
    # The rewrite's reason for existing: cost per node drops from O(N^2)
    # to O(N). If this ever returns a square array again the speedup is
    # gone whatever the timings say.
    root, hands, equity_table, reach = _equivalence_tree(num_hands=5)
    node_data = {}
    returned = cfr._solve_recurse(
        root, reach["OOP"].copy(), reach["IP"].copy(), "OOP", node_data,
        equity_table, "OOP", "IP",
    )
    assert returned.shape == (5,)
    matrix_returned = cfr._solve_recurse_matrix(
        root, reach["OOP"].copy(), reach["IP"].copy(), "OOP", {},
        equity_table, "OOP", "IP",
    )
    assert matrix_returned.shape == (5, 5)


def test_solve_uses_the_vector_recursion_by_default():
    # `_recurse` exists only so the equivalence tests can drive the
    # replaced implementation through the real loop. Production must not
    # be getting the old one by accident.
    calls = []
    original = cfr._solve_recurse

    def spy(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    root, hands, equity_table, reach = _equivalence_tree(num_hands=3)
    cfr._solve_recurse = spy
    try:
        solve(root, hands, equity_table, iterations=4, positions=("OOP", "IP"),
              initial_reach=reach)
    finally:
        cfr._solve_recurse = original
    assert len(calls) == 4
