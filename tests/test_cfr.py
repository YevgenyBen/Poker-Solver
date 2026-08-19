import numpy as np
import pytest

from poker_solver.cards import Card
from poker_solver.cfr import InfoSetTable, mccfr_solve, solve
from poker_solver.chance import ChanceBranch, ChanceNode
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
    assert np.allclose(avg.sum(axis=1), 1.0)
