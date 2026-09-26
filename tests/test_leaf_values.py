"""`bench/leaf_values.py` - what a turn leaf is worth with the river played.

The load-bearing test here is the first one: the walker must reproduce
the engine's OWN terminal convention exactly. If it does not, a depth
study's two arms differ in their accounting as well as their depth, and
that is precisely the confound (F45) the leaf-value seam exists to
remove.
"""
import numpy as np
import pytest

from bench import leaf_values
from poker_solver import cfr
from poker_solver.game_tree import StreetConfig, build_street_tree


def _tree(pot=10.0, stack=20.0):
    return build_street_tree(StreetConfig(
        positions=("OOP", "IP"), pot=pot, stack_bb=stack,
        raise_sizes=(0.75,), max_raises=2))


def _terminals(root):
    seen, stack = [], [root]
    while stack:
        node = stack.pop()
        children = getattr(node, "children", None)
        if children:
            stack.extend(children.values())
        else:
            seen.append(node)
    return seen


def _equity(n, seed=5):
    rng = np.random.default_rng(seed)
    raw = rng.random((n, n))
    table = (raw + (1 - raw.T)) / 2
    np.fill_diagonal(table, 0.5)
    return table.astype(np.float64)


# -- the convention it must match ---------------------------------------

@pytest.mark.parametrize("n", [3, 6])
def test_a_terminal_is_valued_exactly_as_the_engine_values_it(n):
    """The engine's default is `equity * pot - invested_a`, with a fold
    paying `pot - invested_a` or `-invested_a`. Any other convention here
    would make a depth study measure its own accounting."""
    table = _equity(n)
    for terminal in _terminals(_tree()):
        got = leaf_values.value_matrix(
            terminal, position_a="OOP", position_b="IP",
            equity_table=table, strategy_of=lambda node: None)
        invested = terminal.invested["OOP"]
        if "OOP" in terminal.folded:
            want = np.full((n, n), -invested)
        elif "IP" in terminal.folded:
            want = np.full((n, n), terminal.pot - invested)
        else:
            want = table * terminal.pot - invested
        assert np.array_equal(got, want), "convention drifted from the engine's"


def test_the_matrix_walk_agrees_with_the_engines_own_vector_at_a_terminal():
    """Cross-checked against `cfr._terminal_value_vector` itself rather
    than against a re-derivation of it (M164: a study that rebuilds the
    production path measures its own reconstruction)."""
    n = 5
    table = _equity(n)
    reach = np.array([0.4, 0.3, 0.2, 0.05, 0.05])
    for terminal in _terminals(_tree()):
        matrix = leaf_values.value_matrix(
            terminal, position_a="OOP", position_b="IP",
            equity_table=table, strategy_of=lambda node: None)
        as_a = cfr._terminal_value_vector(terminal, table, "OOP", "IP", True, reach)
        as_b = cfr._terminal_value_vector(terminal, table, "OOP", "IP", False, reach)
        assert np.allclose(matrix @ reach, as_a, atol=1e-12)
        assert np.allclose(-(matrix.T @ reach), as_b, atol=1e-12)


# -- the acting player weights the right axis ---------------------------

def test_each_player_weights_its_own_axis():
    """The table is indexed [a_hand, b_hand], so which axis a strategy
    multiplies is not interchangeable - getting it backwards prices hero
    against their own range, which is the error M275 found costing 7.8 bb
    where the truth was 1.84."""
    n = 2
    table = _equity(n)
    root = _tree()

    # A pure strategy for whoever acts: always the FIRST action.
    def first_action(node):
        strategy = np.zeros((n, len(node.legal_actions)))
        strategy[:, 0] = 1.0
        return strategy

    got = leaf_values.value_matrix(root, position_a="OOP", position_b="IP",
                                   equity_table=table, strategy_of=first_action)
    # Following the first action from the root deterministically lands on
    # one leaf, so the value must equal that leaf's own matrix.
    node = root
    while getattr(node, "children", None):
        node = list(node.children.values())[0]
    want = leaf_values.value_matrix(node, position_a="OOP", position_b="IP",
                                    equity_table=table, strategy_of=first_action)
    assert np.allclose(got, want)


def test_a_hand_specific_strategy_is_applied_per_hand():
    """Row `i` of the strategy belongs to hand `i`. A walker that applied
    one averaged row to every hand would look right on a symmetric
    fixture and be wrong on a real one."""
    n = 2
    table = _equity(n)
    root = _tree()
    actions = len(root.legal_actions)

    def split(node):
        strategy = np.zeros((n, len(node.legal_actions)))
        strategy[0, 0] = 1.0                      # hand 0 takes the first
        strategy[1, min(1, len(node.legal_actions) - 1)] = 1.0
        return strategy

    got = leaf_values.value_matrix(root, position_a="OOP", position_b="IP",
                                   equity_table=table, strategy_of=split)
    assert actions > 1
    assert not np.allclose(got[0], got[1]), "the two hands took different lines"


# -- the collapse that makes it affordable ------------------------------

def test_leaves_that_are_the_same_river_situation_share_a_key():
    """M247 measured 27 showdown terminals collapsing to 8 situations.
    The key is what turns 27 river campaigns into 8."""
    root = _tree()
    showdowns = [t for t in _terminals(root) if not t.folded]
    keys = {leaf_values.leaf_key(t, "OOP") for t in showdowns}
    assert len(keys) < len(showdowns), "no collapse means no affordable study"


def test_two_leaves_with_different_money_do_not_share_a_key():
    """The collapse must not merge leaves whose river would be played at
    a different pot or depth - that is M124's bucket error one street
    later."""
    root = _tree()
    showdowns = [t for t in _terminals(root) if not t.folded]
    pots = {round(t.pot, 6) for t in showdowns}
    if len(pots) > 1:
        keyed = {leaf_values.leaf_key(t, "OOP"): t.pot for t in showdowns}
        assert len({round(p, 6) for p in keyed.values()}) == len(pots)


# -- what it refuses -----------------------------------------------------

def test_a_folded_leaf_is_never_given_a_river():
    """The hand ended there. Solving a river for it would invent a street
    that was never played."""
    root = _tree()
    folded = [t for t in _terminals(root) if t.folded]
    assert folded, "the fixture must contain a fold to test this"
    out = leaf_values.turn_leaf_values(
        turn_board=(), hero_range={}, villain_range={}, positions=("OOP", "IP"),
        effective_stack_bb=20.0, terminals=folded, raise_sizes=(0.75,),
        max_raises=2, iterations=1, deck=())
    assert out == {}


def test_an_all_in_leaf_has_no_river_betting_to_solve():
    """Nothing is behind, so the river has no decisions - valuing it by a
    solve would be solving an empty tree."""
    root = _tree(pot=10.0, stack=20.0)
    showdowns = [t for t in _terminals(root) if not t.folded]
    deepest = max(showdowns, key=lambda t: max(t.invested.values()))
    out = leaf_values.turn_leaf_values(
        turn_board=(), hero_range={}, villain_range={}, positions=("OOP", "IP"),
        effective_stack_bb=max(deepest.invested.values()), terminals=[deepest],
        raise_sizes=(0.75,), max_raises=2, iterations=1, deck=())
    assert out == {}


# -- end to end on a real board -----------------------------------------

def test_a_real_river_value_differs_from_its_equity_default():
    """The whole premise: playing the river out is not the same as
    valuing it at showdown equity. If these matched, M247's 1.75-1.86 bb
    spread could not exist and the route would be dead."""
    from poker_solver.board_equity import build_board_equity_table
    from poker_solver.cards import parse_cards
    from poker_solver.combos import HandCombo

    board = tuple(parse_cards("Kd7c2h9s4d"))
    combos = [HandCombo(*parse_cards(c)) for c in ("AhAs", "QhQd", "3h4s", "8c8d")]
    ranges = {c: 1.0 for c in combos}
    pot, stack = 10.0, 20.0

    solved = leaf_values.solved_value(
        board=board, hero_range=ranges, villain_range=ranges, pot=pot,
        effective_stack_bb=stack, positions=("OOP", "IP"),
        raise_sizes=(0.75,), max_raises=2, iterations=40)

    equity = np.nan_to_num(build_board_equity_table(board, sorted(combos, key=str)),
                           nan=0.5)
    default = equity * pot - 0.0

    assert solved.shape == default.shape
    assert not np.allclose(solved, default), (
        "a solved river equals its showdown-equity default - then there is "
        "nothing for a leaf value to carry")
